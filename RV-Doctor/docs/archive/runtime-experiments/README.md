# 早期运行时实验

这些源码来自原 `scratch/`，保留用于复查早期设计与问题定位：

- `runtime.c`、`runtime.S`、`link.ld`：早期自研半主机运行时，与正式运行时存在功能重叠，未接入 `compile_source`。
- `semi_test.S`、`semi_probe.c`：半主机入口与压缩指令识别实验。
- `float_probe.c`：R9 软浮点规则的早期探针。

正式启动代码与链接脚本位于 `compiler/rvcrt0.S`、`compiler/link.ld`。此处属于历史实验资料，不作为当前正确性保证或推荐运行方式；例如 `float_probe.c` 的 8 元素数组在最后一次循环访问 `px[i + 2]` 时会越界，不应直接用作有效基准。
