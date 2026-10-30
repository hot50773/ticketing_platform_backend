--[[
    清除座位的 sold bit。

    ARGV[1] event_id
    ARGV[2] zone_id -> [seat_serial, ...] 的 JSON
--]]
local SOLD_KEY_TPL = "seat_sold_bitmap:event:%s:zone:%s"
local NOT_SOLD_VALUE = 0

local event_id = ARGV[1]
local zone_id_to_seat_serials_map = cjson.decode(ARGV[2])

for zone_id, seat_serials in pairs(zone_id_to_seat_serials_map) do
    local sold_key = string.format(SOLD_KEY_TPL, event_id, zone_id)

    -- 一個 zone 一次 BITFIELD，與 lock_seats 的批次寫法對稱
    local cmd = {'BITFIELD', sold_key}
    for _, seat_serial in ipairs(seat_serials) do
        table.insert(cmd, 'SET')
        table.insert(cmd, 'u1')
        table.insert(cmd, seat_serial)
        table.insert(cmd, NOT_SOLD_VALUE)
    end
    redis.call(unpack(cmd))
end

return 1
