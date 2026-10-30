-- Minimal host for the real addon files; no game client is simulated visually.
mock = { id = 42, locale = "ruRU", title = "Проверка", text = "Привет, герой!", calls = {}, stopped = {}, playing = {}, nextHandle = 0 }
SlashCmdList, UISpecialFrames = {}, {}
UIParent, ChatFontNormal = {}, {}
DEFAULT_CHAT_FRAME = { AddMessage = function(_, text) mock.message = text end }
local methods = {}
function methods:SetScript(name, callback) self.scripts[name] = callback end
function methods:RegisterEvent(event) self.events[event] = true end
function methods:Show() self.visible = true end
function methods:Hide() self.visible = false end
function methods:SetText(text) self.text = text end
function methods:SetEnabled(enabled) self.enabled = enabled end
function methods:CreateFontString() return CreateFrame("FontString") end
for _, name in ipairs({ "SetSize", "SetPoint", "SetFrameStrata", "SetClampedToScreen", "SetMovable", "EnableMouse", "RegisterForDrag", "StartMoving", "StopMovingOrSizing", "SetBackdrop", "SetBackdropBorderColor", "SetWidth", "SetJustifyH", "SetMultiLine", "SetFontObject", "SetAutoFocus", "SetMaxLetters", "SetScrollChild", "SetFocus", "HighlightText" }) do
    methods[name] = function() end
end
function CreateFrame(kind, name, parent, template)
    local frame = setmetatable({ scripts = {}, events = {} }, { __index = methods })
    if template == "BasicFrameTemplateWithInset" then frame.TitleText = CreateFrame("FontString") end
    return frame
end
function GetLocale() return mock.locale end
function UnitSex() return mock.sex or 2 end
function GetQuestID() return mock.id end
function GetTitleText() return mock.title end
function GetQuestText() return mock.text end
function GetProgressText() return "Ещё не всё?" end
function GetRewardText() return "Отличная работа!" end
function UnitName(unit) if not mock.noNPC and unit == "npc" then return "Тестовый NPC" end end
function UnitGUID(unit) if not mock.noNPC and unit == "npc" then return "Creature-0-0-0-0-123-00000001" end end
function strsplit(separator, text)
    local result = {}
    for item in string.gmatch(text, "[^" .. separator .. "]+") do result[#result + 1] = item end
    return unpack(result)
end
function PlaySoundFile(path, channel)
    mock.calls[#mock.calls + 1] = { path = path, channel = channel }
    if mock.failPlay then return false, nil end
    mock.nextHandle = mock.nextHandle + 1
    mock.playing[mock.nextHandle] = true
    return true, mock.nextHandle
end
function StopSound(handle)
    mock.stopped[#mock.stopped + 1] = handle
    mock.playing[handle] = false
end
C_Sound = { IsPlaying = function(handle) return mock.playing[handle] == true end }
function fire(event, argument) WarcraftAudi.events.scripts.OnEvent(WarcraftAudi.events, event, argument) end
function tick(seconds) WarcraftAudi.events.scripts.OnUpdate(WarcraftAudi.events, seconds) end
