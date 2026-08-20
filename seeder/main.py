import asyncio
import pathlib
from pathlib import Path

from ._runner  import SeedingRunner
from ._seeders import PostgresSeeder
from ._providers import CSVNicknameProvider

current_dir = Path(__file__).resolve().parent
file_path = current_dir / "data/nicknames.csv"

async def main():
    # Instantiate the clean seeder setup
    provider = CSVNicknameProvider(file_path=str(file_path))
    seeders = [
        PostgresSeeder(batch_size=1000)
    ]
    
    await SeedingRunner(provider, seeders).run()

if __name__ == "__main__":
    asyncio.run(main())
