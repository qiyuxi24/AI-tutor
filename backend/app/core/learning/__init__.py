"""学习会话域（Phase B1）：把"下一步做什么"从提示词与临时属性里拿出来，交给一个一等对象。

模块：
    session.py —— `LearnTurn`（一轮学习的判定）+ `learning_sessions` 的读写语义

存储（`knowledge.db` 的建表与 SQL）在 `app/core/knowledge_graph.py`，与
`set_section_learn` / `read_learn_state` 同处 —— knowledge.db 的表定义只有那一处。
"""
