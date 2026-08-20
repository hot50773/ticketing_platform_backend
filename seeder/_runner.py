import time
from seeder._providers import DataProvider
from seeder._seeders import DataSeeder

class SeedingRunner:
    """Orchestrates seeding flow using injected provider and seeders."""
    def __init__(self, provider: DataProvider, seeders: list[DataSeeder]):
        self.provider = provider
        self.seeders = seeders

    async def run(self) -> None:
        print("Fetching data from provider...")
        start_fetch = time.perf_counter()
        data = self.provider.get_data()
        print(f"Loaded {len(data)} records in {time.perf_counter() - start_fetch:.2f}s.")

        for seeder in self.seeders:
            seeder_name = seeder.__class__.__name__
            
            print(f"[{seeder_name}] Clearing existing data...")
            await seeder.clear()
            
            print(f"[{seeder_name}] Seeding data...")
            start_seed = time.perf_counter()
            await seeder.seed(data)
            duration = time.perf_counter() - start_seed
            print(f"[{seeder_name}] Seeding completed in {duration:.2f}s.")

        print("Seeding orchestration completed successfully.")
