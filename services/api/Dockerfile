# 明路 API 镜像。多阶段：装依赖与拷源码分开，改代码不用重装依赖。
FROM python:3.12-slim AS builder

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

COPY --from=builder /install /usr/local

WORKDIR /srv
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini pytest.ini ./

# 非 root 运行
RUN useradd -r -u 10001 minglu && chown -R minglu:minglu /srv
USER minglu

ENV MINGLU_ENV=prod \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 注意：容器不跑 alembic。架构 §7.3 禁止在发布脚本里跑 DDL，
# 迁移由 docker-compose 里一次性的 migrate 服务先跑完再起 api。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
