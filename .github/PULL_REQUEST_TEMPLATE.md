## 这个 PR 做了什么

<!-- 一句话说清。关联 issue：Fixes #xx -->

## 类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 文档 / 注释
- [ ] 重构（行为不变）
- [ ] 测试
- [ ] 工具链 / CI

## ⚠️ 必勾项（这是这个项目特有的规矩）

- [ ] **我改了 `hub/src/whalehub/` 的片段，并且跑过 `python3 hub/tools/build_single.py`**
- [ ] **我把重新生成的 `hub/hub.py` 一起提交了**（CI 会逐字节比对，没同步必红）
- [ ] 我跑过 `python3 -m unittest discover tests`
- [ ] 我的改动**没有引入第三方运行时依赖**（CI 有零依赖护栏）
- [ ] 用户可感知的改动，我更新了 `CHANGELOG.md`

> 只改文档/CI/测试的 PR，前两项可以勾"不适用"。

## 怎么验证的

<!-- 贴命令和结果。别只写"测试通过" -->

```bash
$ python3 hub/tools/build_single.py
$ cmp hub/hub.py hub/dist/hub.py && echo 一致
一致
$ python3 -m unittest discover tests
...
```

## 有没有副作用

<!-- 改表结构？改 API 契约？改存档格式？没有就写「无」 -->

- [ ] 涉及数据库 schema 变更（若有，说明迁移方案）
- [ ] 涉及 HTTP 端点契约变更（若有，说明兼容性）
- [ ] 涉及配置文件格式变更

## 截图 / 输出（若是用户可见的改动）

<!-- 挂件改动、说话效果改动，贴个图比文字有用 -->
