import asyncio
from datetime import datetime, timezone, timedelta
from sqlmodel import select
from app.db.postgres import Database
from app.models._models import Venue, Zone, Seat, Event, EventZone, EventSeat
from app.enums import SeatStatus

async def seed_concert_data():
    db = Database()
    async with db.get_async_session() as session:
        print("Seeding Venue, Zones, Seats, Event, EventZones, and EventSeats...")
        
        # 1. Create Venue
        venue = Venue(
            name="Taipei Arena",
            description="The primary indoor arena in Taipei, Taiwan.",
            address="No. 2, Sec. 4, Nanjing E. Rd., Songshan Dist., Taipei City"
        )
        session.add(venue)
        await session.flush() # populated venue.id
        
        # 2. Create Zones (Zone A, Zone B, Zone C)
        zones = [
            Zone(code="Zone A", venue_id=venue.id),
            Zone(code="Zone B", venue_id=venue.id),
            Zone(code="Zone C", venue_id=venue.id)
        ]
        session.add_all(zones)
        await session.flush()
        
        # 3. Create physical Seats for each Zone
        seats = []
        for zone in zones:
            for row in range(1, 3): # 2 rows
                for col in range(1, 4): # 3 columns
                    serial_number = (row - 1) * 3 + col
                    seat = Seat(
                        serial_number=serial_number,
                        row=row,
                        column=col,
                        zone_id=zone.id
                    )
                    seats.append(seat)
        session.add_all(seats)
        await session.flush()
        
        # 4. Create Event
        start_time = datetime.now(timezone.utc) + timedelta(days=30)
        end_time = start_time + timedelta(hours=3)
        event = Event(
            name="Mayday 2026 Concert",
            description="Mayday Concert Tour Live at Taipei Arena",
            start_at=start_time.replace(tzinfo=None),
            end_at=str(end_time.replace(tzinfo=None)),
            venue_id=venue.id
        )
        session.add(event)
        await session.flush()
        
        # 5. Create EventZone mapping (Zone prices)
        prices = {
            "Zone A": 4800,
            "Zone B": 3800,
            "Zone C": 2800
        }
        event_zones = []
        zone_price_map = {}
        for zone in zones:
            price = prices.get(zone.code, 1000)
            ez = EventZone(
                event_id=event.id,
                zone_id=zone.id,
                price=price
            )
            event_zones.append(ez)
        session.add_all(event_zones)
        await session.flush()
        
        for ez in event_zones:
            # find zone code
            for zone in zones:
                if zone.id == ez.zone_id:
                    zone_price_map[zone.id] = ez.id
        
        # 6. Create EventSeat status map
        event_seats = []
        for seat in seats:
            ez_id = zone_price_map[seat.zone_id]
            es = EventSeat(
                event_zone_id=ez_id,
                seat_id=seat.id,
                status=SeatStatus.RELEASED
            )
            event_seats.append(es)
        session.add_all(event_seats)
        
        await session.commit()
        print("Successfully seeded Mayday Concert layout!")
        print(f"Event ID: {event.id}")
        print(f"Venue ID: {venue.id}")
        for zone in zones:
            print(f"Zone: {zone.code} (ID: {zone.id})")

if __name__ == "__main__":
    asyncio.run(seed_concert_data())
