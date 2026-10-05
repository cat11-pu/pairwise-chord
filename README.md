# pairwise-chord

纯内存的 Chord 一致性哈希环仿真内核，只用 Python 标准库，所有节点、
finger 表与键值数据都保存在进程内存里，不涉及任何真实网络通信。

## 目录内容

- chord/core.py：环内核，负责哈希映射、路由、finger 表、节点加入与退出、数据副本
- tests/test_core.py：环内核的验收测试

## 跑测试

    python3 -m unittest discover -s tests -v

Windows 上把 python3 换成 python 即可。
