# NOTE: Assertions have been autogenerated by utils/update_mca_test_checks.py
# RUN: llvm-mca -mtriple=x86_64-unknown-unknown -mcpu=btver2 -resource-pressure=false -retire-stats -iterations=1 < %s | FileCheck %s

  vsqrtps %xmm0, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2
  vaddps  %xmm0, %xmm1, %xmm2

# CHECK:      Iterations:        1
# CHECK-NEXT: Instructions:      16
# CHECK-NEXT: Total Cycles:      31
# CHECK-NEXT: Total uOps:        16

# CHECK:      Dispatch Width:    2
# CHECK-NEXT: uOps Per Cycle:    0.52
# CHECK-NEXT: IPC:               0.52
# CHECK-NEXT: Block RThroughput: 21.0

# CHECK:      Instruction Info:
# CHECK-NEXT: [1]: #uOps
# CHECK-NEXT: [2]: Latency
# CHECK-NEXT: [3]: RThroughput
# CHECK-NEXT: [4]: MayLoad
# CHECK-NEXT: [5]: MayStore
# CHECK-NEXT: [6]: HasSideEffects (U)

# CHECK:      [1]    [2]    [3]    [4]    [5]    [6]    Instructions:
# CHECK-NEXT:  1      21    21.00                       vsqrtps	%xmm0, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2
# CHECK-NEXT:  1      3     1.00                        vaddps	%xmm0, %xmm1, %xmm2

# CHECK:      Retire Control Unit - number of cycles where we saw N instructions retired:
# CHECK-NEXT: [# retired], [# cycles]
# CHECK-NEXT:  0,           23  (74.2%)
# CHECK-NEXT:  2,           8  (25.8%)

# CHECK:      Total ROB Entries:                64
# CHECK-NEXT: Max Used ROB Entries:             16  ( 25.0% )
# CHECK-NEXT: Average Used ROB Entries per cy:  11  ( 17.2% )
