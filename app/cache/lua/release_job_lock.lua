--[[
    只在鎖還是自己的時候才刪。

    GET 之後再 DEL 不行：中間 TTL 可能到期、別人取得了鎖，那個 DEL 就會把
    別人的鎖刪掉 —— 兩個 runner 同時以為自己是唯一的那個，比沒有鎖更糟。

    KEYS[1] 鎖的 key
    ARGV[1] 取得時寫入的 token
--]]
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
