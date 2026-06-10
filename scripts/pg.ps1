# 本地 Postgres 启停助手(平台化开发,Windows / 免安装 zip 版)
#
# 用法:
#   .\scripts\pg.ps1 start     启动
#   .\scripts\pg.ps1 stop      停止
#   .\scripts\pg.ps1 status    状态
#   .\scripts\pg.ps1 init      首次初始化数据目录 + 建库(已存在则跳过)
#   .\scripts\pg.ps1 psql      打开 psql 连到 tagent 库
#
# 路径可用环境变量覆盖(两地机器若装在别处):
#   $env:PG_HOME(默认 C:\pgsql)  $env:PG_DATA(默认 C:\pgdata)
# 注意:数据目录在项目外,不进 git;两地各自独立,靠 Alembic 迁移保 schema 一致。

param([Parameter(Position = 0)][string]$cmd = "status")

$PG_HOME = if ($env:PG_HOME) { $env:PG_HOME } else { "C:\pgsql" }
$PG_DATA = if ($env:PG_DATA) { $env:PG_DATA } else { "C:\pgdata" }
$BIN = Join-Path $PG_HOME "bin"

switch ($cmd) {
    "init" {
        if (Test-Path (Join-Path $PG_DATA "PG_VERSION")) {
            Write-Host "数据目录已存在,跳过 initdb:$PG_DATA"
        }
        else {
            & "$BIN\initdb.exe" -D $PG_DATA -U postgres -E UTF8 --locale=C -A trust
        }
        & "$BIN\pg_ctl.exe" -D $PG_DATA -l (Join-Path $PG_DATA "pg.log") start
        Start-Sleep -Seconds 2
        & "$BIN\createdb.exe" -U postgres -h localhost tagent 2>$null
        & "$BIN\createdb.exe" -U postgres -h localhost tagent_test 2>$null
        Write-Host "已建库 tagent / tagent_test。连接串:"
        Write-Host "  DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/tagent"
        Write-Host "  DATABASE_URL_TEST_PG=postgresql+asyncpg://postgres@localhost:5432/tagent_test"
    }
    "start" { & "$BIN\pg_ctl.exe" -D $PG_DATA -l (Join-Path $PG_DATA "pg.log") start }
    "stop" { & "$BIN\pg_ctl.exe" -D $PG_DATA stop }
    "status" { & "$BIN\pg_ctl.exe" -D $PG_DATA status }
    "psql" { & "$BIN\psql.exe" -U postgres -h localhost -d tagent }
    default { Write-Host "用法: .\scripts\pg.ps1 [init|start|stop|status|psql]" }
}
