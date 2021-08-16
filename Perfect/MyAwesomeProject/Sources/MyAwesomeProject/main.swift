
import PerfectHTTP
import PerfectHTTPServer
 
// 注册您自己的路由和请求／响应句柄
var routes = Routes()
routes.add(method: .get, uri: "/") {
    request, response in
    response.setHeader(.contentType, value: "text/html")
    response.appendBody(string: "<html><title>Hello, world!</title><body>Hello, world!</body></html>")
        .completed()
}
 
do {
    // 启动HTTP服务器
    try HTTPServer.launch(
        .server(name: "www.example.ca", port: 8181, routes: routes))
} catch {
    fatalError("\(error)") // fatal error launching one of the servers
}