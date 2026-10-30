local addonName, A = ...
WarcraftAudi = A
A.version = "0.2.0"
A.voices = {}
local stages = { QUEST_DETAIL = "detail", QUEST_PROGRESS = "progress", QUEST_COMPLETE = "complete" }
local getters = { detail = GetQuestText, progress = GetProgressText, complete = GetRewardText }
local frame = CreateFrame("Frame")
A.events = frame

function A:Notify(text)
    DEFAULT_CHAT_FRAME:AddMessage("|cffd9ad63Warcraft Audi:|r " .. text)
end

function A:Refresh()
    if self.UpdateUI then self:UpdateUI() end
end

function A:RegisterPack(name, entries)
    if type(name) ~= "string" or type(entries) ~= "table" then return false, "invalid pack" end
    local pending = {}
    for _, entry in ipairs(entries) do
        if type(entry.questID) ~= "number" or entry.questID <= 0 or entry.questID % 1 ~= 0
            or type(entry.locale) ~= "string" or not getters[entry.stage]
            or type(entry.files) ~= "table" or #entry.files == 0 then
            return false, "invalid entry"
        end
        if entry.gender ~= nil and entry.gender ~= "male" and entry.gender ~= "female" then
            return false, "invalid gender"
        end
        for _, file in ipairs(entry.files) do
            if type(file) ~= "string" or file == "" then return false, "invalid audio path" end
        end
        local key = entry.locale .. ":" .. entry.questID .. ":" .. entry.stage
        if entry.gender then key = key .. ":" .. entry.gender end
        if self.voices[key] or pending[key] then return false, "duplicate entry: " .. key end
        pending[key] = { pack = name, files = entry.files }
    end
    for key, entry in pairs(pending) do self.voices[key] = entry end
    self:Refresh()
    return true
end

function A:GetVoice()
    if not self.current then return nil end
    local gender = UnitSex("player") == 3 and "female" or "male"
    return self.voices[self.current.key .. ":" .. gender] or self.voices[self.current.key]
end

function A:Stop()
    -- Clear ownership before StopSound: a completion callback must not advance this queue.
    local handle = self.soundHandle
    if handle or self.queue then self.status = "Остановлено" end
    self.soundHandle, self.queue, self.queueIndex = nil, nil, nil
    self.pollElapsed = 0
    if handle then StopSound(handle, 150) end
    self:Refresh()
end

function A:NextClip()
    if not self.queue then return end
    self.queueIndex = self.queueIndex + 1
    local path = self.queue[self.queueIndex]
    if not path then
        self.queue, self.queueIndex, self.soundHandle = nil, nil, nil
        self.status = "Воспроизведение завершено"
        self:Refresh()
        return
    end
    local played, handle = PlaySoundFile(path, "Dialog")
    if not played or not handle then
        self:Stop()
        self.status = "Не удалось воспроизвести запись. Проверьте файлы пакета и звук «Диалоги»."
    else
        self.soundHandle = handle
        self.pollElapsed = 0
        self.status = "Звучит реплика " .. self.queueIndex .. "/" .. #self.queue
    end
    self:Refresh()
end

function A:Play()
    self:Stop()
    if not self.current then return end
    local entry = self:GetVoice()
    if not entry then
        self.status = "Для этого текста ещё нет записи. Он доступен в экспорте."
        self:Refresh()
        return
    end
    self.queue, self.queueIndex = entry.files, 0
    self:NextClip()
end

function A:Demo()
    self:Stop()
    self.queue = { "Interface\\AddOns\\WarcraftAudi\\Audio\\demo.ogg" }
    self.queueIndex = 0
    if self.panel then self.panel:Show() end
    self:NextClip()
end

local function clean(text)
    return (text or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
        :gsub("|H.-|h(.-)|h", "%1"):gsub("|T.-|t", ""):gsub("|A.-|a", "")
end

function A:OnQuest(stage)
    self:Stop()
    local questID = GetQuestID()
    if type(questID) ~= "number" or questID <= 0 then return end
    local locale = GetLocale()
    local text = clean(getters[stage]())
    local speaker = UnitName("npc") or UnitName("questnpc") or UnitName("target") or "Неизвестный NPC"
    local guid = UnitGUID("npc") or UnitGUID("questnpc")
    local npcID = guid and tonumber((select(6, strsplit("-", guid)))) or nil
    local key = locale .. ":" .. questID .. ":" .. stage
    self.current = { key = key, questID = questID, stage = stage, title = clean(GetTitleText()), speaker = speaker }
    if self.db.settings.collect and text ~= "" then
        self.db.collected[key] = {
            quest_id = questID, locale = locale, stage = stage, text = text,
            title = self.current.title, speaker = speaker, npc_id = npcID,
        }
    end
    self.status = self:GetVoice() and "Запись найдена" or "Записи пока нет"
    self:Refresh()
    if self.panel then self.panel:Show() end
    if self.db.settings.autoPlay then self:Play() end
end

local function quote(value)
    local escaped = tostring(value):gsub('[%z\1-\31\\"]', function(char)
        if char == '"' then return '\\"' end
        if char == '\\' then return '\\\\' end
        return string.format("\\u%04x", string.byte(char))
    end)
    return '"' .. escaped .. '"'
end

function A:ExportJSON()
    local keys, rows = {}, {}
    for key in pairs(self.db.collected) do keys[#keys + 1] = key end
    table.sort(keys)
    for _, key in ipairs(keys) do
        local row = self.db.collected[key]
        local fields = {}
        for _, field in ipairs({ "quest_id", "locale", "stage", "title", "speaker", "npc_id", "text" }) do
            if row[field] ~= nil then
                fields[#fields + 1] = quote(field) .. ":" .. (type(row[field]) == "number" and row[field] or quote(row[field]))
            end
        end
        fields[#fields + 1] = '"audio":[]'
        rows[#rows + 1] = "{" .. table.concat(fields, ",") .. "}"
    end
    return '{"schema_version":1,"quests":[' .. table.concat(rows, ",\n") .. "]}"
end

function A:ToggleAuto()
    self.db.settings.autoPlay = not self.db.settings.autoPlay
    if not self.db.settings.autoPlay then self:Stop() end
    self:Refresh()
end

frame:SetScript("OnUpdate", function(_, elapsed)
    if not A.soundHandle then return end
    A.pollElapsed = (A.pollElapsed or 0) + elapsed
    if A.pollElapsed < 0.25 then return end
    A.pollElapsed = 0
    if not C_Sound.IsPlaying(A.soundHandle) then
        A.soundHandle = nil
        A:NextClip()
    end
end)

frame:SetScript("OnEvent", function(_, event, name)
    if event == "ADDON_LOADED" then
        if name ~= addonName then return end
        WarcraftAudiDB = WarcraftAudiDB or {}
        A.db = WarcraftAudiDB
        A.db.settings = A.db.settings or {}
        A.db.collected = A.db.collected or {}
        if A.db.settings.autoPlay == nil then A.db.settings.autoPlay = true end
        if A.db.settings.collect == nil then A.db.settings.collect = true end
        A.db.schemaVersion = 1
        A:Refresh()
    elseif event == "QUEST_FINISHED" then
        A:Stop()
        A.current = nil
        if A.panel then A.panel:Hide() end
    elseif A.db and stages[event] then
        A:OnQuest(stages[event])
    end
end)
for _, event in ipairs({ "ADDON_LOADED", "QUEST_DETAIL", "QUEST_PROGRESS", "QUEST_COMPLETE", "QUEST_FINISHED" }) do
    frame:RegisterEvent(event)
end

SLASH_WARCRAFTAUDI1 = "/wca"
SLASH_WARCRAFTAUDI2 = "/waudi"
SlashCmdList.WARCRAFTAUDI = function(command)
    command = (command or ""):lower():match("^%s*(.-)%s*$")
    if not A.db then return end
    if command == "play" then A:Play()
    elseif command == "demo" then A:Demo()
    elseif command == "stop" then A:Stop(); A.status = "Остановлено"; A:Refresh()
    elseif command == "auto" then A:ToggleAuto()
    elseif command == "export" then A:ShowExport()
    elseif command == "collect" then
        A.db.settings.collect = not A.db.settings.collect
        A:Notify("Сбор текстов: " .. (A.db.settings.collect and "включён" or "выключен"))
    elseif command == "" then A.panel:Show()
    else A:Notify("/wca — панель; demo — проба голоса; play — повтор; stop — стоп; auto — автозапуск; export — тексты; collect — сбор текстов.") end
end
