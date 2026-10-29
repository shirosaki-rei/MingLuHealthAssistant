"""modules 包。

架构 §2.2 的依赖规则（写进 CI）：
- modules 之间只能通过 service 层调用，禁止跨模块直接查表
- safety 不得依赖 llm_gateway
- entitlement 被所有人调用，但不反向依赖任何业务模块
- platform 不知道 modules 的存在
"""
