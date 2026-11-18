# Contributing

开发协议以 [AGENTS.md](AGENTS.md) 为准。这里是最短路径。

1. 从 `origin/main` 拉分支：`git fetch && git switch -c cursor/<task> origin/main`。
2. PR 标题带 lane 标签：`[qis]` `[corr]` `[exotic]` `[market]` `[platform]`。
3. 合并前跑 `npm run build` 和 `PYTHONPATH=backend pytest backend/tests -q`。CI 会再跑一遍。
4. 不要 stash 搬功能，不要从 `research/*` 覆盖 `main`，不要在非 `main` 上 `./deploy.sh`。
5. 密钥与 `cffex_vol.sqlite` 不进 git。Wind 用 `WIND_USER` / `WIND_PASSWORD` / `WIND_DSN`。
