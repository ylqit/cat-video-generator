# Docker Compose 最简部署

本方案只运行一个应用容器。Vue 在镜像构建阶段编译，FastAPI 在运行阶段同时托管
前端静态文件和 `/api/v1`，不需要 Nginx、独立前端容器或任务队列。

## 运行结构

```text
浏览器 → http://服务器IP:8765 → FastAPI → PostgreSQL / Ark / 本地媒体卷
```

容器监听 `0.0.0.0:8765`，Compose 使用 `8765:8765` 发布到宿主机全部网卡。
当前没有登录系统，所有能连接该端口的人都能调用生产接口，包括显式确认后的付费
Ark 请求。因此只能在可信网络中运行，公网暴露由宿主机防火墙和云安全组负责限制。

## 服务器准备

Linux 服务器需要 Docker Engine 和 Docker Compose v2。进入项目目录后创建配置与持久目录：

```bash
cp .env.example .env
chmod 600 .env
mkdir -p docker-data/work docker-data/assets docker-data/diagnostics
```

在 `.env` 中填写 Ark 和 PostgreSQL 配置。不要把 `.env` 提交到 Git，也不要写入镜像。
Compose 会在容器中覆盖以下路径：

```text
MEDIA_WORK_ROOT=/data/work
MEDIA_ASSET_ROOT=/data/assets
FFMPEG_PATH=/usr/bin/ffmpeg
FFPROBE_PATH=/usr/bin/ffprobe
```

镜像以 UID/GID `10001` 运行。如果宿主机目录不可写，执行：

```bash
sudo chown -R 10001:10001 docker-data
```

## 构建与启动

已有 V3 数据库升级前，先使用同一 `.env` 执行只运行一次的归档清理；全新数据库可跳过：

```bash
docker compose build
docker compose run --rm app python scripts/archive_v3_and_clear.py
```

Manifest 会写入宿主机 `docker-data/diagnostics`。不要绕过 `0015` 的旧业务记录检查。

```bash
docker compose build
docker compose run --rm app alembic upgrade head
docker compose run --rm app cvg doctor
docker compose up -d
```

容器每次启动都会先执行 `alembic upgrade head`。迁移失败时应用不会在旧表结构上继续
运行。启动后访问：

```text
http://服务器IP:8765
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f app
```

健康检查地址：

```text
http://服务器IP:8765/api/v1/health
```

## 更新与停止

```bash
git pull
docker compose build
docker compose up -d
```

停止服务：

```bash
docker compose down
```

`docker compose down` 不会删除 `docker-data`。不要使用 `docker compose down -v`，也不要
手工删除持久目录。

## Canon 与项目

V4不迁移旧生产项目，但 `0015` 会原样保留数据库中已经批准的 Canon 记录与本地文件。
Canon 页面只读展示这些长期资产；每个新项目都可在镜头工作台上传外部参考图，并明确
指定为生成参考或最终锚点，不要求先建立新的全局 Canon。

```text
person:headshot
person:fullbody
cat:front
cat:side
cat:back
style:line_texture
style:indoor
style:outdoor
```

项目上传文件会写入 `/data/assets`。确认迁移保留的 Canon 可以预览后，再创建镜头片段项目。

## 本地开发

后端：

```powershell
uv sync --frozen
uv run cvg doctor
uv run cvg api
```

前端：

```powershell
cd web
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 代理到本机 `8765`。

验证与容器相同的单服务模式：

```powershell
cd web
npm ci
npm run build
cd ..
uv run cvg api --static-dir web/dist
```

访问 `http://127.0.0.1:8765`。
