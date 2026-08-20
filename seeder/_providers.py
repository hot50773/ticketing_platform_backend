import csv
import os
from abc import ABC, abstractmethod

class DataProvider(ABC):
    """Abstract base class for data sources."""
    @abstractmethod
    def get_data(self) -> list[str]:
        pass

class CSVNicknameProvider(DataProvider):
    """Reads nickname strings from a CSV file source."""
    def __init__(self, file_path: str):
        self.file_path = file_path

    def get_data(self) -> list[str]:
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Source file {self.file_path} not found.")

        nicknames = []
        with open(self.file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if name := row.get("nick_name"):
                    nicknames.append(name.strip())
        return nicknames
