"""profiler: QEMU bare-metal execution, trace parsing, metric aggregation.

Modules (see docs/03-系统架构设计.md §5.3~5.6):
    runner        -- qemu-system-riscv{32,64} semihosting runner (FR3)
    trace_parser  -- `-d in_asm,exec,nochain` log parser (FR3)
    metrics       -- MetricsReport aggregation (FR3)
    energy        -- relative energy score model (FR3)
"""
