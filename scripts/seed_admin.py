"""Create the first admin user once, from env. Run: VIREONET_ADMIN_USER=admin VIREONET_ADMIN_PASSWORD=<12+ chars> python scripts/seed_admin.py"""

import asyncio
import os
import sys

sys.path.insert(0, "src")

from vireonet.config import get_settings
from vireonet.db import create_engine, create_session_factory
from vireonet.iam.service import UserService


async def main() -> None:
    settings = get_settings()
    username = os.environ.get("VIREONET_ADMIN_USER") or settings.admin_user
    password = os.environ.get("VIREONET_ADMIN_PASSWORD") or settings.admin_password
    if not username or not password:
        print("Set VIREONET_ADMIN_USER and VIREONET_ADMIN_PASSWORD (12+ chars). Nothing created.")
        return
    svc = UserService(create_session_factory(create_engine(str(settings.database_url))))
    users = await svc.list()
    if users:
        print(f"Users already exist ({len(users)}). Nothing created.")
        return
    await svc.create(username=username, display_name=username, password=password, role="admin")
    print(f"Admin user {username} created.")


if __name__ == "__main__":
    asyncio.run(main())
