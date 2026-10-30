local addonName, A = ...
local floor = math.floor
local bridge = CreateFrame("Frame", "WarcraftAudiBridge", UIParent)
A.bridge = bridge
bridge:SetSize(208, 112)
bridge:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 12, -12)
bridge:SetFrameStrata("TOOLTIP")
bridge:SetClampedToScreen(true)
bridge:SetMovable(true)
bridge:EnableMouse(true)
bridge:RegisterForDrag("LeftButton")
bridge:SetScript("OnDragStart", bridge.StartMoving)
bridge:SetScript("OnDragStop", bridge.StopMovingOrSizing)
if UIParent.GetEffectiveScale then bridge:SetScale(1 / UIParent:GetEffectiveScale()) end
local background = bridge:CreateTexture(nil, "BACKGROUND")
background:SetAllPoints()
background:SetColorTexture(0, 0, 0, 1)
local function marker(x, y, r, g, b)
    local texture = bridge:CreateTexture(nil, "OVERLAY")
    texture:SetSize(6, 6)
    texture:SetPoint("TOPLEFT", x, -y)
    texture:SetColorTexture(r, g, b, 1)
end
marker(0, 0, 1, 0, 1)
marker(202, 0, 0, 1, 1)
marker(0, 106, 1, 1, 0)
bridge.pixels, bridge.previous = {}, {}
for bit = 0, 2047 do
    local texture = bridge:CreateTexture(nil, "ARTWORK")
    texture:SetSize(3, 3)
    texture:SetPoint("TOPLEFT", 8 + (bit % 64) * 3, -(8 + floor(bit / 64) * 3))
    texture:SetColorTexture(0, 0, 0, 1)
    bridge.pixels[bit + 1] = texture
end
bridge:Hide()
bridge.session = math.random(1, 2147483647)
bridge.sequence, bridge.page, bridge.elapsed = 0, 0, 0

local function uint32(value)
    return string.char(floor(value / 16777216) % 256, floor(value / 65536) % 256,
        floor(value / 256) % 256, value % 256)
end
local function adler(data)
    local a, b = 1, 0
    for i = 1, #data do
        a = (a + string.byte(data, i)) % 65521
        b = (b + a) % 65521
    end
    return b * 65536 + a
end
function bridge:Packet(part)
    local fragment = self.message:sub(part * 232 + 1, (part + 1) * 232)
    local body = "WCA1" .. uint32(self.session) .. uint32(self.sequence)
        .. string.char(part, self.count, #fragment, 0) .. uint32(self.checksum)
        .. fragment .. string.rep("\0", 232 - #fragment)
    return body .. uint32(adler(body))
end
function bridge:Draw()
    if not self.message then return end
    local packet = self:Packet(self.page)
    for bit = 0, 2047 do
        local value = floor(string.byte(packet, floor(bit / 8) + 1) / 2 ^ (7 - bit % 8)) % 2
        if self.previous[bit + 1] ~= value then
            self.pixels[bit + 1]:SetColorTexture(value, value, value, 1)
            self.previous[bit + 1] = value
        end
    end
end
function bridge:Send(message)
    if #message > 232 * 128 then return false end
    self.message, self.checksum = message, adler(message)
    self.count = math.max(1, math.ceil(#message / 232))
    self.sequence = (self.sequence + 1) % 4294967296
    self.page, self.elapsed = 0, 0
    self:Draw()
    self:Show()
    return true
end
bridge:SetScript("OnUpdate", function(self, elapsed)
    self.elapsed = self.elapsed + elapsed
    if self.elapsed < 0.12 or not self.message then return end
    self.elapsed = 0
    self.page = (self.page + 1) % self.count
    self:Draw()
end)

function A:BridgeEvent(action)
    if not self.db or not self.db.settings.companion then return end
    local fields = { '"v":1', '"action":' .. self.JSONQuote(action) }
    if self.current and action ~= "stop" and action ~= "demo" then
        for _, key in ipairs({ "quest_id", "stage", "locale", "gender", "text", "speaker", "npc_id", "npc_race", "npc_type", "npc_gender", "player_name" }) do
            local value = self.current[key]
            if value ~= nil then
                fields[#fields + 1] = self.JSONQuote(key) .. ":" .. (type(value) == "number" and value or self.JSONQuote(value))
            end
        end
    end
    if not bridge:Send("{" .. table.concat(fields, ",") .. "}") then
        self.status = "Текст слишком длинный для передачи внешней программе"
        self:Refresh()
    end
end
function A:ToggleCompanion()
    self:Stop()
    self.db.settings.companion = not self.db.settings.companion
    if self.db.settings.companion then self:BridgeEvent("stop") else bridge:Hide() end
    self:Notify("Внешняя программа: " .. (self.db.settings.companion and "включена. Выделите цветной индикатор в программе." or "выключена"))
    self:Refresh()
end
bridge:RegisterEvent("PLAYER_LOGIN")
bridge:SetScript("OnEvent", function()
    if A.db and A.db.settings.companion then A:BridgeEvent("stop") end
end)
