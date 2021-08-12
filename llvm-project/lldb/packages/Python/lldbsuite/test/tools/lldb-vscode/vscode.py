#!/usr/bin/env python

import binascii
import json
import optparse
import os
import pprint
import socket
import string
import subprocess
import sys
import threading
import time


def dump_memory(base_addr, data, num_per_line, outfile):

    data_len = len(data)
    hex_string = binascii.hexlify(data)
    addr = base_addr
    ascii_str = ''
    i = 0
    while i < data_len:
        outfile.write('0x%8.8x: ' % (addr + i))
        bytes_left = data_len - i
        if bytes_left >= num_per_line:
            curr_data_len = num_per_line
        else:
            curr_data_len = bytes_left
        hex_start_idx = i * 2
        hex_end_idx = hex_start_idx + curr_data_len * 2
        curr_hex_str = hex_string[hex_start_idx:hex_end_idx]
        # 'curr_hex_str' now contains the hex byte string for the
        # current line with no spaces between bytes
        t = iter(curr_hex_str)
        # Print hex bytes separated by space
        outfile.write(' '.join(a + b for a, b in zip(t, t)))
        # Print two spaces
        outfile.write('  ')
        # Calculate ASCII string for bytes into 'ascii_str'
        ascii_str = ''
        for j in range(i, i + curr_data_len):
            ch = data[j]
            if ch in string.printable and ch not in string.whitespace:
                ascii_str += '%c' % (ch)
            else:
                ascii_str += '.'
        # Print ASCII representation and newline
        outfile.write(ascii_str)
        i = i + curr_data_len
        outfile.write('\n')


def read_packet(f, verbose=False, trace_file=None):
    '''Decode a JSON packet that starts with the content length and is
       followed by the JSON bytes from a file 'f'. Returns None on EOF.
    '''
    line = f.readline().decode("utf-8")
    if len(line) == 0:
        return None  # EOF.

    # Watch for line that starts with the prefix
    prefix = 'Content-Length: '
    if line.startswith(prefix):
        # Decode length of JSON bytes
        if verbose:
            print('content: "%s"' % (line))
        length = int(line[len(prefix):])
        if verbose:
            print('length: "%u"' % (length))
        # Skip empty line
        line = f.readline()
        if verbose:
            print('empty: "%s"' % (line))
        # Read JSON bytes
        json_str = f.read(length)
        if verbose:
            print('json: "%s"' % (json_str))
        if trace_file:
            trace_file.write('from adaptor:\n%s\n' % (json_str))
        # Decode the JSON bytes into a python dictionary
        return json.loads(json_str)

    return None


def packet_type_is(packet, packet_type):
    return 'type' in packet and packet['type'] == packet_type


def read_packet_thread(vs_comm):
    done = False
    while not done:
        packet = read_packet(vs_comm.recv, trace_file=vs_comm.trace_file)
        # `packet` will be `None` on EOF. We want to pass it down to
        # handle_recv_packet anyway so the main thread can handle unexpected
        # termination of lldb-vscode and stop waiting for new packets.
        done = not vs_comm.handle_recv_packet(packet)


class DebugCommunication(object):

    def __init__(self, recv, send, init_commands):
        self.trace_file = None
        self.send = send
        self.recv = recv
        self.recv_packets = []
        self.recv_condition = threading.Condition()
        self.recv_thread = threading.Thread(target=read_packet_thread,
                                            args=(self,))
        self.process_event_body = None
        self.exit_status = None
        self.initialize_body = None
        self.thread_stop_reasons = {}
        self.breakpoint_events = []
        self.module_events = {}
        self.sequence = 1
        self.threads = None
        self.recv_thread.start()
        self.output_condition = threading.Condition()
        self.output = {}
        self.configuration_done_sent = False
        self.frame_scopes = {}
        self.init_commands = init_commands

    @classmethod
    def encode_content(cls, s):
        return ("Content-Length: %u\r\n\r\n%s" % (len(s), s)).encode("utf-8")

    @classmethod
    def validate_response(cls, command, response):
        if command['command'] != response['command']:
            raise ValueError('command mismatch in response')
        if command['seq'] != response['request_seq']:
            raise ValueError('seq mismatch in response')

    def get_active_modules(self):
        return self.module_events
        
    def get_output(self, category, timeout=0.0, clear=True):
        self.output_condition.acquire()
        output = None
        if category in self.output:
            output = self.output[category]
            if clear:
                del self.output[category]
        elif timeout != 0.0:
            self.output_condition.wait(timeout)
            if category in self.output:
                output = self.output[category]
                if clear:
                    del self.output[category]
        self.output_condition.release()
        return output

    def collect_output(self, category, duration, clear=True):
        end_time = time.time() + duration
        collected_output = ""
        while end_time > time.time():
            output = self.get_output(category, timeout=0.25, clear=clear)
            if output:
                collected_output += output
        return collected_output if collected_output else None

    def enqueue_recv_packet(self, packet):
        self.recv_condition.acquire()
        self.recv_packets.append(packet)
        self.recv_condition.notify()
        self.recv_condition.release()

    def handle_recv_packet(self, packet):
        '''Called by the read thread that is waiting for all incoming packets
           to store the incoming packet in "self.recv_packets" in a thread safe
           way. This function will then signal the "self.recv_condition" to
           indicate a new packet is available. Returns True if the caller
           should keep calling this function for more packets.
        '''
        # If EOF, notify the read thread by enqueuing a None.
        if not packet:
            self.enqueue_recv_packet(None)
            return False

        # Check the packet to see if is an event packet
        keepGoing = True
        packet_type = packet['type']
        if packet_type == 'event':
            event = packet['event']
            body = None
            if 'body' in packet:
                body = packet['body']
            # Handle the event packet and cache information from these packets
            # as they come in
            if event == 'output':
                # Store any output we receive so clients can retrieve it later.
                category = body['category']
                output = body['output']
                self.output_condition.acquire()
                if category in self.output:
                    self.output[category] += output
                else:
                    self.output[category] = output
                self.output_condition.notify()
                self.output_condition.release()
                # no need to add 'output' event packets to our packets list
                return keepGoing
            elif event == 'process':
                # When a new process is attached or launched, remember the
                # details that are available in the body of the event
                self.process_event_body = body
            elif event == 'stopped':
                # Each thread that stops with a reason will send a
                # 'stopped' event. We need to remember the thread stop
                # reasons since the 'threads' command doesn't return
                # that information.
                self._process_stopped()
                tid = body['threadId']
                self.thread_stop_reasons[tid] = body
            elif event == 'breakpoint':
                # Breakpoint events come in when a breakpoint has locations
                # added or removed. Keep track of them so we can look for them
                # in tests.
                self.breakpoint_events.append(packet)
                # no need to add 'breakpoint' event packets to our packets list
                return keepGoing
            elif event == 'module':
                reason = body['reason']
                if (reason == 'new' or reason == 'changed'):
                    self.module_events[body['module']['name']] = body['module']
                elif reason == 'removed':
                    if body['module']['name'] in self.module_events:
                        self.module_events.pop(body['module']['name'])
                return keepGoing

        elif packet_type == 'response':
            if packet['command'] == 'disconnect':
                keepGoing = False
        self.enqueue_recv_packet(packet)
        return keepGoing

    def send_packet(self, command_dict, set_sequence=True):
        '''Take the "command_dict" python dictionary and encode it as a JSON
           string and send the contents as a packet to the VSCode debug
           adaptor'''
        # Set the sequence ID for this command automatically
        if set_sequence:
            command_dict['seq'] = self.sequence
            self.sequence += 1
        # Encode our command dictionary as a JSON string
        json_str = json.dumps(command_dict, separators=(',', ':'))
        if self.trace_file:
            self.trace_file.write('to adaptor:\n%s\n' % (json_str))
        length = len(json_str)
        if length > 0:
            # Send the encoded JSON packet and flush the 'send' file
            self.send.write(self.encode_content(json_str))
            self.send.flush()

    def recv_packet(self, filter_type=None, filter_event=None, timeout=None):
        '''Get a JSON packet from the VSCode debug adaptor. This function
           assumes a thread that reads packets is running and will deliver
           any received packets by calling handle_recv_packet(...). This
           function will wait for the packet to arrive and return it when
           it does.'''
        while True:
            try:
                self.recv_condition.acquire()
                packet = None
                while True:
                    for (i, curr_packet) in enumerate(self.recv_packets):
                        if not curr_packet:
                            raise EOFError
                        packet_type = curr_packet['type']
                        if filter_type is None or packet_type in filter_type:
                            if (filter_event is None or
                                (packet_type == 'event' and
                                 curr_packet['event'] in filter_event)):
                                packet = self.recv_packets.pop(i)
                                break
                    if packet:
                        break
                    # Sleep until packet is received
                    len_before = len(self.recv_packets)
                    self.recv_condition.wait(timeout)
                    len_after = len(self.recv_packets)
                    if len_before == len_after:
                        return None  # Timed out
                return packet
            except EOFError:
                return None
            finally:
                self.recv_condition.release()

        return None

    def send_recv(self, command):
        '''Send a command python dictionary as JSON and receive the JSON
           response. Validates that the response is the correct sequence and
           command in the reply. Any events that are received are added to the
           events list in this object'''
        self.send_packet(command)
        done = False
        while not done:
            response = self.recv_packet(filter_type='response')
            if response is None:
                desc = 'no response for "%s"' % (command['command'])
                raise ValueError(desc)
            self.validate_response(command, response)
            return response
        return None

    def wait_for_event(self, filter=None, timeout=None):
        while True:
            return self.recv_packet(filter_type='event', filter_event=filter,
                                    timeout=timeout)
        return None

    def wait_for_stopped(self, timeout=None):
        stopped_events = []
        stopped_event = self.wait_for_event(filter=['stopped', 'exited'],
                                            timeout=timeout)
        exited = False
        while stopped_event:
            stopped_events.append(stopped_event)
            # If we exited, then we are done
            if stopped_event['event'] == 'exited':
                self.exit_status = stopped_event['body']['exitCode']
                exited = True
                break
            # Otherwise we stopped and there might be one or more 'stopped'
            # events for each thread that stopped with a reason, so keep
            # checking for more 'stopped' events and return all of them
            stopped_event = self.wait_for_event(filter='stopped', timeout=0.25)
        if exited:
            self.threads = []
        return stopped_events

    def wait_for_exited(self):
        event_dict = self.wait_for_event('exited')
        if event_dict is None:
            raise ValueError("didn't get stopped event")
        return event_dict

    def get_initialize_value(self, key):
        '''Get a value for the given key if it there is a key/value pair in
           the "initialize" request response body.
        '''
        if self.initialize_body and key in self.initialize_body:
            return self.initialize_body[key]
        return None

    def get_threads(self):
        if self.threads is None:
            self.request_threads()
        return self.threads

    def get_thread_id(self, threadIndex=0):
        '''Utility function to get the first thread ID in the thread list.
           If the thread list is empty, then fetch the threads.
        '''
        if self.threads is None:
            self.request_threads()
        if self.threads and threadIndex < len(self.threads):
            return self.threads[threadIndex]['id']
        return None

    def get_stackFrame(self, frameIndex=0, threadId=None):
        '''Get a single "StackFrame" object from a "stackTrace" request and
           return the "StackFrame as a python dictionary, or None on failure
        '''
        if threadId is None:
            threadId = self.get_thread_id()
        if threadId is None:
            print('invalid threadId')
            return None
        response = self.request_stackTrace(threadId, startFrame=frameIndex,
                                           levels=1)
        if response:
            return response['body']['stackFrames'][0]
        print('invalid response')
        return None

    def get_completions(self, text):
        response = self.request_completions(text)
        return response['body']['targets']

    def get_scope_variables(self, scope_name, frameIndex=0, threadId=None):
        stackFrame = self.get_stackFrame(frameIndex=frameIndex,
                                         threadId=threadId)
        if stackFrame is None:
            return []
        frameId = stackFrame['id']
        if frameId in self.frame_scopes:
            frame_scopes = self.frame_scopes[frameId]
        else:
            scopes_response = self.request_scopes(frameId)
            frame_scopes = scopes_response['body']['scopes']
            self.frame_scopes[frameId] = frame_scopes
        for scope in frame_scopes:
            if scope['name'] == scope_name:
                varRef = scope['variablesReference']
                variables_response = self.request_variables(varRef)
                if variables_response:
                    if 'body' in variables_response:
                        body = variables_response['body']
                        if 'variables' in body:
                            vars = body['variables']
                            return vars
        return []

    def get_global_variables(self, frameIndex=0, threadId=None):
        return self.get_scope_variables('Globals', frameIndex=frameIndex,
                                        threadId=threadId)

    def get_local_variables(self, frameIndex=0, threadId=None):
        return self.get_scope_variables('Locals', frameIndex=frameIndex,
                                        threadId=threadId)

    def get_local_variable(self, name, frameIndex=0, threadId=None):
        locals = self.get_local_variables(frameIndex=frameIndex,
                                          threadId=threadId)
        for local in locals:
            if 'name' in local and local['name'] == name:
                return local
        return None

    def get_local_variable_value(self, name, frameIndex=0, threadId=None):
        variable = self.get_local_variable(name, frameIndex=frameIndex,
                                           threadId=threadId)
        if variable and 'value' in variable:
            return variable['value']
        return None

    def replay_packets(self, replay_file_path):
        f = open(replay_file_path, 'r')
        mode = 'invalid'
        set_sequence = False
        command_dict = None
        while mode != 'eof':
            if mode == 'invalid':
                line = f.readline()
                if line.startswith('to adapter:'):
                    mode = 'send'
                elif line.startswith('from adapter:'):
                    mode = 'recv'
            elif mode == 'send':
                command_dict = read_packet(f)
                # Skip the end of line that follows the JSON
                f.readline()
                if command_dict is None:
                    raise ValueError('decode packet failed from replay file')
                print('Sending:')
                pprint.PrettyPrinter(indent=2).pprint(command_dict)
                # raw_input('Press ENTER to send:')
                self.send_packet(command_dict, set_sequence)
                mode = 'invalid'
            elif mode == 'recv':
                print('Replay response:')
                replay_response = read_packet(f)
                # Skip the end of line that follows the JSON
                f.readline()
                pprint.PrettyPrinter(indent=2).pprint(replay_response)
                actual_response = self.recv_packet()
                if actual_response:
                    type = actual_response['type']
                    print('Actual response:')
                    if type == 'response':
                        self.validate_response(command_dict, actual_response)
                    pprint.PrettyPrinter(indent=2).pprint(actual_response)
                else:
                    print("error: didn't get a valid response")
                mode = 'invalid'

    def request_attach(self, program=None, pid=None, waitFor=None, trace=None,
                       initCommands=None, preRunCommands=None,
                       stopCommands=None, exitCommands=None,
                       attachCommands=None, terminateCommands=None,
                       coreFile=None):
        args_dict = {}
        if pid is not None:
            args_dict['pid'] = pid
        if program is not None:
            args_dict['program'] = program
        if waitFor is not None:
            args_dict['waitFor'] = waitFor
        if trace:
            args_dict['trace'] = trace
        args_dict['initCommands'] = self.init_commands
        if initCommands:
            args_dict['initCommands'].extend(initCommands)
        if preRunCommands:
            args_dict['preRunCommands'] = preRunCommands
        if stopCommands:
            args_dict['stopCommands'] = stopCommands
        if exitCommands:
            args_dict['exitCommands'] = exitCommands
        if terminateCommands:
            args_dict['terminateCommands'] = terminateCommands
        if attachCommands:
            args_dict['attachCommands'] = attachCommands
        if coreFile:
            args_dict['coreFile'] = coreFile
        command_dict = {
            'command': 'attach',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_configurationDone(self):
        command_dict = {
            'command': 'configurationDone',
            'type': 'request',
            'arguments': {}
        }
        response = self.send_recv(command_dict)
        if response:
            self.configuration_done_sent = True
        return response

    def _process_stopped(self):
        self.threads = None
        self.frame_scopes = {}

    def request_continue(self, threadId=None):
        if self.exit_status is not None:
            raise ValueError('request_continue called after process exited')
        # If we have launched or attached, then the first continue is done by
        # sending the 'configurationDone' request
        if not self.configuration_done_sent:
            return self.request_configurationDone()
        args_dict = {}
        if threadId is None:
            threadId = self.get_thread_id()
        args_dict['threadId'] = threadId
        command_dict = {
            'command': 'continue',
            'type': 'request',
            'arguments': args_dict
        }
        response = self.send_recv(command_dict)
        # Caller must still call wait_for_stopped.
        return response

    def request_disconnect(self, terminateDebuggee=None):
        args_dict = {}
        if terminateDebuggee is not None:
            if terminateDebuggee:
                args_dict['terminateDebuggee'] = True
            else:
                args_dict['terminateDebuggee'] = False
        command_dict = {
            'command': 'disconnect',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_evaluate(self, expression, frameIndex=0, threadId=None):
        stackFrame = self.get_stackFrame(frameIndex=frameIndex,
                                         threadId=threadId)
        if stackFrame is None:
            return []
        args_dict = {
            'expression': expression,
            'frameId': stackFrame['id'],
        }
        command_dict = {
            'command': 'evaluate',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_initialize(self):
        command_dict = {
            'command': 'initialize',
            'type': 'request',
            'arguments': {
                'adapterID': 'lldb-native',
                'clientID': 'vscode',
                'columnsStartAt1': True,
                'linesStartAt1': True,
                'locale': 'en-us',
                'pathFormat': 'path',
                'supportsRunInTerminalRequest': True,
                'supportsVariablePaging': True,
                'supportsVariableType': True
            }
        }
        response = self.send_recv(command_dict)
        if response:
            if 'body' in response:
                self.initialize_body = response['body']
        return response

    def request_launch(self, program, args=None, cwd=None, env=None,
                       stopOnEntry=False, disableASLR=True,
                       disableSTDIO=False, shellExpandArguments=False,
                       trace=False, initCommands=None, preRunCommands=None,
                       stopCommands=None, exitCommands=None,
                       terminateCommands=None ,sourcePath=None,
                       debuggerRoot=None, launchCommands=None, sourceMap=None):
        args_dict = {
            'program': program
        }
        if args:
            args_dict['args'] = args
        if cwd:
            args_dict['cwd'] = cwd
        if env:
            args_dict['env'] = env
        if stopOnEntry:
            args_dict['stopOnEntry'] = stopOnEntry
        if disableASLR:
            args_dict['disableASLR'] = disableASLR
        if disableSTDIO:
            args_dict['disableSTDIO'] = disableSTDIO
        if shellExpandArguments:
            args_dict['shellExpandArguments'] = shellExpandArguments
        if trace:
            args_dict['trace'] = trace
        args_dict['initCommands'] = self.init_commands
        if initCommands:
            args_dict['initCommands'].extend(initCommands)
        if preRunCommands:
            args_dict['preRunCommands'] = preRunCommands
        if stopCommands:
            args_dict['stopCommands'] = stopCommands
        if exitCommands:
            args_dict['exitCommands'] = exitCommands
        if terminateCommands:
            args_dict['terminateCommands'] = terminateCommands
        if sourcePath:
            args_dict['sourcePath'] = sourcePath
        if debuggerRoot:
            args_dict['debuggerRoot'] = debuggerRoot
        if launchCommands:
            args_dict['launchCommands'] = launchCommands
        if sourceMap:
            args_dict['sourceMap'] = sourceMap
        command_dict = {
            'command': 'launch',
            'type': 'request',
            'arguments': args_dict
        }
        response = self.send_recv(command_dict)

        # Wait for a 'process' and 'initialized' event in any order
        self.wait_for_event(filter=['process', 'initialized'])
        self.wait_for_event(filter=['process', 'initialized'])
        return response

    def request_next(self, threadId):
        if self.exit_status is not None:
            raise ValueError('request_continue called after process exited')
        args_dict = {'threadId': threadId}
        command_dict = {
            'command': 'next',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_stepIn(self, threadId):
        if self.exit_status is not None:
            raise ValueError('request_continue called after process exited')
        args_dict = {'threadId': threadId}
        command_dict = {
            'command': 'stepIn',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_stepOut(self, threadId):
        if self.exit_status is not None:
            raise ValueError('request_continue called after process exited')
        args_dict = {'threadId': threadId}
        command_dict = {
            'command': 'stepOut',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_pause(self, threadId=None):
        if self.exit_status is not None:
            raise ValueError('request_continue called after process exited')
        if threadId is None:
            threadId = self.get_thread_id()
        args_dict = {'threadId': threadId}
        command_dict = {
            'command': 'pause',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_scopes(self, frameId):
        args_dict = {'frameId': frameId}
        command_dict = {
            'command': 'scopes',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_setBreakpoints(self, file_path, line_array, condition=None,
                               hitCondition=None):
        (dir, base) = os.path.split(file_path)
        breakpoints = []
        for line in line_array:
            bp = {'line': line}
            if condition is not None:
                bp['condition'] = condition
            if hitCondition is not None:
                bp['hitCondition'] = hitCondition
            breakpoints.append(bp)
        source_dict = {
            'name': base,
            'path': file_path
        }
        args_dict = {
            'source': source_dict,
            'breakpoints': breakpoints,
            'lines': '%s' % (line_array),
            'sourceModified': False,
        }
        command_dict = {
            'command': 'setBreakpoints',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_setExceptionBreakpoints(self, filters):
        args_dict = {'filters': filters}
        command_dict = {
            'command': 'setExceptionBreakpoints',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_setFunctionBreakpoints(self, names, condition=None,
                                       hitCondition=None):
        breakpoints = []
        for name in names:
            bp = {'name': name}
            if condition is not None:
                bp['condition'] = condition
            if hitCondition is not None:
                bp['hitCondition'] = hitCondition
            breakpoints.append(bp)
        args_dict = {'breakpoints': breakpoints}
        command_dict = {
            'command': 'setFunctionBreakpoints',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_getCompileUnits(self, moduleId):
        args_dict = {'moduleId': moduleId}
        command_dict = {
            'command': 'getCompileUnits',
            'type': 'request',
            'arguments': args_dict
        }
        response = self.send_recv(command_dict)
        return response
        
    def request_completions(self, text):
        args_dict = {
            'text': text,
            'column': len(text)
        }
        command_dict = {
            'command': 'completions',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_stackTrace(self, threadId=None, startFrame=None, levels=None,
                           dump=False):
        if threadId is None:
            threadId = self.get_thread_id()
        args_dict = {'threadId': threadId}
        if startFrame is not None:
            args_dict['startFrame'] = startFrame
        if levels is not None:
            args_dict['levels'] = levels
        command_dict = {
            'command': 'stackTrace',
            'type': 'request',
            'arguments': args_dict
        }
        response = self.send_recv(command_dict)
        if dump:
            for (idx, frame) in enumerate(response['body']['stackFrames']):
                name = frame['name']
                if 'line' in frame and 'source' in frame:
                    source = frame['source']
                    if 'sourceReference' not in source:
                        if 'name' in source:
                            source_name = source['name']
                            line = frame['line']
                            print("[%3u] %s @ %s:%u" % (idx, name, source_name,
                                                        line))
                            continue
                print("[%3u] %s" % (idx, name))
        return response

    def request_threads(self):
        '''Request a list of all threads and combine any information from any
           "stopped" events since those contain more information about why a
           thread actually stopped. Returns an array of thread dictionaries
           with information about all threads'''
        command_dict = {
            'command': 'threads',
            'type': 'request',
            'arguments': {}
        }
        response = self.send_recv(command_dict)
        body = response['body']
        # Fill in "self.threads" correctly so that clients that call
        # self.get_threads() or self.get_thread_id(...) can get information
        # on threads when the process is stopped.
        if 'threads' in body:
            self.threads = body['threads']
            for thread in self.threads:
                # Copy the thread dictionary so we can add key/value pairs to
                # it without affecting the original info from the "threads"
                # command.
                tid = thread['id']
                if tid in self.thread_stop_reasons:
                    thread_stop_info = self.thread_stop_reasons[tid]
                    copy_keys = ['reason', 'description', 'text']
                    for key in copy_keys:
                        if key in thread_stop_info:
                            thread[key] = thread_stop_info[key]
        else:
            self.threads = None
        return response

    def request_variables(self, variablesReference, start=None, count=None):
        args_dict = {'variablesReference': variablesReference}
        if start is not None:
            args_dict['start'] = start
        if count is not None:
            args_dict['count'] = count
        command_dict = {
            'command': 'variables',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_setVariable(self, containingVarRef, name, value, id=None):
        args_dict = {
            'variablesReference': containingVarRef,
            'name': name,
            'value': str(value)
        }
        if id is not None:
            args_dict['id'] = id
        command_dict = {
            'command': 'setVariable',
            'type': 'request',
            'arguments': args_dict
        }
        return self.send_recv(command_dict)

    def request_testGetTargetBreakpoints(self):
        '''A request packet used in the LLDB test suite to get all currently
           set breakpoint infos for all breakpoints currently set in the
           target.
        '''
        command_dict = {
            'command': '_testGetTargetBreakpoints',
            'type': 'request',
            'arguments': {}
        }
        return self.send_recv(command_dict)

    def terminate(self):
        self.send.close()
        # self.recv.close()


class DebugAdaptor(DebugCommunication):
    def __init__(self, executable=None, port=None, init_commands=[], log_file=None):
        self.process = None
        if executable is not None:
            adaptor_env = os.environ.copy()
            if log_file:
                adaptor_env['LLDBVSCODE_LOG'] = log_file
            self.process = subprocess.Popen([executable],
                                            stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE,
                                            env=adaptor_env)
            DebugCommunication.__init__(self, self.process.stdout,
                                        self.process.stdin, init_commands)
        elif port is not None:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(('127.0.0.1', port))
            DebugCommunication.__init__(self, s.makefile('r'), s.makefile('w'),
                init_commands)

    def get_pid(self):
        if self.process:
            return self.process.pid
        return -1

    def terminate(self):
        super(DebugAdaptor, self).terminate()
        if self.process is not None:
            self.process.terminate()
            self.process.wait()
            self.process = None


def attach_options_specified(options):
    if options.pid is not None:
        return True
    if options.waitFor:
        return True
    if options.attach:
        return True
    if options.attachCmds:
        return True
    return False


def run_vscode(dbg, args, options):
    dbg.request_initialize()
    if attach_options_specified(options):
        response = dbg.request_attach(program=options.program,
                                      pid=options.pid,
                                      waitFor=options.waitFor,
                                      attachCommands=options.attachCmds,
                                      initCommands=options.initCmds,
                                      preRunCommands=options.preRunCmds,
                                      stopCommands=options.stopCmds,
                                      exitCommands=options.exitCmds,
                                      terminateCommands=options.terminateCmds)
    else:
        response = dbg.request_launch(options.program,
                                      args=args,
                                      env=options.envs,
                                      cwd=options.workingDir,
                                      debuggerRoot=options.debuggerRoot,
                                      sourcePath=options.sourcePath,
                                      initCommands=options.initCmds,
                                      preRunCommands=options.preRunCmds,
                                      stopCommands=options.stopCmds,
                                      exitCommands=options.exitCmds,
                                      terminateCommands=options.terminateCmds)

    if response['success']:
        if options.sourceBreakpoints:
            source_to_lines = {}
            for file_line in options.sourceBreakpoints:
                (path, line) = file_line.split(':')
                if len(path) == 0 or len(line) == 0:
                    print('error: invalid source with line "%s"' %
                          (file_line))

                else:
                    if path in source_to_lines:
                        source_to_lines[path].append(int(line))
                    else:
                        source_to_lines[path] = [int(line)]
            for source in source_to_lines:
                dbg.request_setBreakpoints(source, source_to_lines[source])
        if options.funcBreakpoints:
            dbg.request_setFunctionBreakpoints(options.funcBreakpoints)
        dbg.request_configurationDone()
        dbg.wait_for_stopped()
    else:
        if 'message' in response:
            print(response['message'])
    dbg.request_disconnect(terminateDebuggee=True)


def main():
    parser = optparse.OptionParser(
        description=('A testing framework for the Visual Studio Code Debug '
                     'Adaptor protocol'))

    parser.add_option(
        '--vscode',
        type='string',
        dest='vscode_path',
        help=('The path to the command line program that implements the '
              'Visual Studio Code Debug Adaptor protocol.'),
        default=None)

    parser.add_option(
        '--program',
        type='string',
        dest='program',
        help='The path to the program to debug.',
        default=None)

    parser.add_option(
        '--workingDir',
        type='string',
        dest='workingDir',
        default=None,
        help='Set the working directory for the process we launch.')

    parser.add_option(
        '--sourcePath',
        type='string',
        dest='sourcePath',
        default=None,
        help=('Set the relative source root for any debug info that has '
              'relative paths in it.'))

    parser.add_option(
        '--debuggerRoot',
        type='string',
        dest='debuggerRoot',
        default=None,
        help=('Set the working directory for lldb-vscode for any object files '
              'with relative paths in the Mach-o debug map.'))

    parser.add_option(
        '-r', '--replay',
        type='string',
        dest='replay',
        help=('Specify a file containing a packet log to replay with the '
              'current Visual Studio Code Debug Adaptor executable.'),
        default=None)

    parser.add_option(
        '-g', '--debug',
        action='store_true',
        dest='debug',
        default=False,
        help='Pause waiting for a debugger to attach to the debug adaptor')

    parser.add_option(
        '--port',
        type='int',
        dest='port',
        help="Attach a socket to a port instead of using STDIN for VSCode",
        default=None)

    parser.add_option(
        '--pid',
        type='int',
        dest='pid',
        help="The process ID to attach to",
        default=None)

    parser.add_option(
        '--attach',
        action='store_true',
        dest='attach',
        default=False,
        help=('Specify this option to attach to a process by name. The '
              'process name is the basename of the executable specified with '
              'the --program option.'))

    parser.add_option(
        '-f', '--function-bp',
        type='string',
        action='append',
        dest='funcBreakpoints',
        help=('Specify the name of a function to break at. '
              'Can be specified more than once.'),
        default=[])

    parser.add_option(
        '-s', '--source-bp',
        type='string',
        action='append',
        dest='sourceBreakpoints',
        default=[],
        help=('Specify source breakpoints to set in the format of '
              '<source>:<line>. '
              'Can be specified more than once.'))

    parser.add_option(
        '--attachCommand',
        type='string',
        action='append',
        dest='attachCmds',
        default=[],
        help=('Specify a LLDB command that will attach to a process. '
              'Can be specified more than once.'))

    parser.add_option(
        '--initCommand',
        type='string',
        action='append',
        dest='initCmds',
        default=[],
        help=('Specify a LLDB command that will be executed before the target '
              'is created. Can be specified more than once.'))

    parser.add_option(
        '--preRunCommand',
        type='string',
        action='append',
        dest='preRunCmds',
        default=[],
        help=('Specify a LLDB command that will be executed after the target '
              'has been created. Can be specified more than once.'))

    parser.add_option(
        '--stopCommand',
        type='string',
        action='append',
        dest='stopCmds',
        default=[],
        help=('Specify a LLDB command that will be executed each time the'
              'process stops. Can be specified more than once.'))

    parser.add_option(
        '--exitCommand',
        type='string',
        action='append',
        dest='exitCmds',
        default=[],
        help=('Specify a LLDB command that will be executed when the process '
              'exits. Can be specified more than once.'))

    parser.add_option(
        '--terminateCommand',
        type='string',
        action='append',
        dest='terminateCmds',
        default=[],
        help=('Specify a LLDB command that will be executed when the debugging '
              'session is terminated. Can be specified more than once.'))

    parser.add_option(
        '--env',
        type='string',
        action='append',
        dest='envs',
        default=[],
        help=('Specify environment variables to pass to the launched '
              'process.'))

    parser.add_option(
        '--waitFor',
        action='store_true',
        dest='waitFor',
        default=False,
        help=('Wait for the next process to be launched whose name matches '
              'the basename of the program specified with the --program '
              'option'))

    (options, args) = parser.parse_args(sys.argv[1:])

    if options.vscode_path is None and options.port is None:
        print('error: must either specify a path to a Visual Studio Code '
              'Debug Adaptor vscode executable path using the --vscode '
              'option, or a port to attach to for an existing lldb-vscode '
              'using the --port option')
        return
    dbg = DebugAdaptor(executable=options.vscode_path, port=options.port)
    if options.debug:
        raw_input('Waiting for debugger to attach pid "%i"' % (
                  dbg.get_pid()))
    if options.replay:
        dbg.replay_packets(options.replay)
    else:
        run_vscode(dbg, args, options)
    dbg.terminate()


if __name__ == '__main__':
    main()