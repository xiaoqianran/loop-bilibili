# packages/

| 包 | 职责 |
|----|------|
| `loop_core` | opencli 运行器、限速 profile、失败分类、进度落盘、路径引导 |
| `bili_subbatch` | **不依赖 opencli**：字幕 SubBatch HTTP/WBI、batch、SRT、pack；**推荐首页 rcmd**（`homepage.py`） |

`main.py` 通过 `ensure_sys_path` 把本目录加入 `sys.path`。

浏览器侧同协议工具见 [`../userscripts/`](../userscripts/)（Tampermonkey）。  
一键安装：[Greasy Fork · Bili SubBatch (loop-bilibili)](https://greasyfork.org/zh-CN/scripts/589638-bili-subbatch-loop-bilibili)
