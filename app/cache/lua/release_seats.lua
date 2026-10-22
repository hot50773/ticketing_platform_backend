--[[
    清除座位的 sold bit，並（可選）移除 pending_release 旗標。

    兩件事必須在同一支 script 裡：若先清 bit 再 SREM，中間買家可以合法鎖走
    該座位，而旗標還在的話下一輪會重跑，把那個合法的 hold 清掉。

    ARGV[1] event_id
    ARGV[2] zone_id -> [seat_serial, ...] 的 JSON
    ARGV[3] 要移除旗標的 order_id；空字串表示這次釋放沒有旗標
            （建單失敗的放回路徑沒有訂單可標）
--]]
local SOLD_KEY_TPL = "seat_sold_bitmap:event:%s:zone:%s"
local PENDING_KEY = "pending_release"
local NOT_SOLD_VALUE = 0

local event_id = ARGV[1]
local zone_id_to_seat_serials_map = cjson.decode(ARGV[2])
local pending_order_id = ARGV[3]

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

if pending_order_id ~= '' then
    redis.call('SREM', PENDING_KEY, string.format("%s:%s", event_id, pending_order_id))
end

return 1
