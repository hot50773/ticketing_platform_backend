local EXISTENCE_KEY_TPL = "seat_existence_bitmap:event:%s:zone:%s"
local SOLD_KEY_TPL = "seat_sold_bitmap:event:%s:zone:%s"
local EXIST_VALUE = 1
local NOT_SOLD_VALUE = 0

-- TODO: change to 1 single bitfield
local event_id = ARGV[1]
local zone_id_to_seat_serials_map = cjson.decode(ARGV[2])

local SUCCESS_FLAG = 1
local ERR_FLAG = 0
local ERROR_CODE_UNEXIST = "SEAT_UNEXIST_ERROR"
local ERROR_CODE_OCCUPIED = "SEAT_OCCUPIED_ERROR"

local function build_error_response (error_code, _event_id, _zone_id, _seat_serial)
    return {ERR_FLAG, error_code, _event_id, _zone_id, _seat_serial}
end

local success_response = {SUCCESS_FLAG}

--[[
    通用點陣圖批次比對函式
    @param key string Redis 的 key
    @param offsets table 包含多個偏移量 (如: seat_serials) 的陣列
    @param target_value number 期望的數值 (0 或 1)
    @return boolean 是否全部符合
    @return number/nil 如果失敗，回傳第一個不符合的 offset 號碼
--]]
local function check_all_match(key, offsets, target_value)
    -- 1. 組裝 BITFIELD 批次指令
    local cmd = {'BITFIELD', key}
    for _, offset in ipairs(offsets) do
        table.insert(cmd, 'GET')
        table.insert(cmd, 'u1') -- bit unit
        table.insert(cmd, offset)
    end

    -- 2. 一次性向 Redis 發出查詢
    local results = redis.call(unpack(cmd))

    -- 3. 依序檢查（實作 Early Return 機制）
    for i, offset in ipairs(offsets) do
        local current_val = results[i] or 0
        if current_val ~= target_value then
            -- 🌟 只要發現不符合，立刻中斷並回傳不符合的 offset
            return false, offset
        end
    end

    -- 4. 全部符合
    return true, nil
end
-- 第一階段：純驗證（唯讀）
for zone_id, seat_serials in pairs(zone_id_to_seat_serials_map) do
    local existence_key = string.format(EXISTENCE_KEY_TPL, event_id, zone_id)
    local sold_key = string.format(SOLD_KEY_TPL, event_id, zone_id)

    -- 🌟 效能優化：利用 BITFIELD 批次串接特性，一次抓取該區域所有要驗證的座位
    is_all_seats_exists, first_unexist_offset = check_all_match(existence_key, seat_serials, EXIST_VALUE)
    if not is_all_seats_exists then
        build_error_response(ERROR_CODE_UNEXIST, event_id, zone_id, first_unexist_offset)
    end

    is_all_seats_orderable, first_occupied_offset = check_all_match(sold_key, seat_serials, NOT_SOLD_VALUE)
    if not is_all_seats_orderable then
        build_error_response(ERROR_CODE_OCCUPIED, event_id, zone_id, first_occupied_offset)
    end
end

-- 第二階段：全面驗證通過後，才進入寫入階段（確保原子性）
for zone_id, seat_serials in pairs(zone_id_to_seat_serials_map) do
    local sold_key = string.format(SOLD_KEY_TPL, event_id, zone_id)

    for _, seat_serial in ipairs(seat_serials) do
        redis.call('BITFIELD', sold_key, 'SET', 'u1', seat_serial, SOLD_VALUE)
    end
end

return success_response




