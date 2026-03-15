import os
import sys

# alembicフォルダの中から、親ディレクトリ（main.pyなどがある場所）のファイルを読み込めるようにパスを追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# プロジェクト内のモデル（設計図）と、データベース接続URLを読み込む
from models import Base
from main import DATABASE_URL

# これはAlembicの設定オブジェクトです
config = context.config

# ログの設定を読み込みます
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# === 修正箇所：Alembicにモデルのメタデータ（設計図の詳細）を渡す ===
target_metadata = Base.metadata

# alembic.iniの設定を、main.pyから読み込んだDATABASE_URLで上書きする
config.set_main_option("sqlalchemy.url", DATABASE_URL)

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()