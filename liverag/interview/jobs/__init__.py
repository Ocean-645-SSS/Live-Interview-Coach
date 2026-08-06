"""Interview Coach 后台异步任务系统。

按类型组织：
- repository.py —— 持久化 Job 的 PostgreSQL CRUD
- queue.py —— Redis 队列与短期锁
- tasks.py —— job_type → 执行函数注册表
- worker.py —— BackgroundWorker 异步主循环
- worker_main.py —— 独立 Worker 进程入口
"""
