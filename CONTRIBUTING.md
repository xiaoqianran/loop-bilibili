# 贡献与模块约定

本仓库布局对齐 [loop-zhihu](https://github.com/xiaoqianran/loop-zhihu)：

```text
packages/loop_core/   # 公共：opencli、限速、进度、错误
modules/<name>/       # 业务：模型 + 采集 + 写出
main.py               # 只做子命令调度
```

## 新增模块清单

1. 在 `modules/<name>/` 建立包，写 `README.md`（opencli 命令、行为、运行方式）。
2. 复用 `loop_core`，**不要**再写一套 subprocess/限速。
3. 在 `main.py` 的 `MODULE_CATALOG` 注册，并加子命令。
4. 高风险接口（字幕、总结、评论）使用**更严** profile，禁止与列表同速。
5. 默认串行；需要并发时在模块 README 说明风险。

## 本地检查

```bash
python3 -m py_compile main.py
python3 main.py modules
python3 main.py catalog --rebuild catalogs/2071007724-海安雨
```
