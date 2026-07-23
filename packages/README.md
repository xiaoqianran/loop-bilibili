# packages/

| 包 | 职责 |
|----|------|
| `loop_core` | opencli 运行器、限速 profile、失败分类、进度落盘、路径引导 |
| `bili_subbatch` | **字幕现行方案**：SubBatch HTTP/WBI、batch、SRT、pack |

`main.py` 通过 `ensure_sys_path` 把本目录加入 `sys.path`。
