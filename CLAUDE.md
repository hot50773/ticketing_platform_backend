# Project Instructions

## Comments

On top of the global commenting rules:

- Three lines max, even for a valuable "why".
- Don't explain what a technique is (FOR SHARE, lazy load, a partial index); if official docs cover it, delete it.
- TODO / FIXME, cross-references, section headers and API-contract notes are fine.
- `.claude/hooks/check_redundant_comments.py` only catches literal restatements; everything else is on you.

## Routers

- Each module in `app/routers/` has a `customer_router` (storefront) and a `manager_router` (everything under `/admin`).
- `manager_router` is guarded at router level with `dependencies=[Depends(get_current_manager)]`, or `require_admin` for admin-only modules. Declare `AnyManager` / `Customer` as a parameter only when the route needs the caller.
- Route function names start with the least role that may call them:
  - `customer_`: storefront, anonymous or a signed-in customer (`customer_get_event`, `customer_create_order`)
  - `manager_`: any manager, ADMIN included (`manager_publish_event`, `manager_delete_order`)
  - `admin_`: ADMIN only (`admin_create_manager`)
