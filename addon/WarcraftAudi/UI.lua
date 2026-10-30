local _, A = ...
local panel = CreateFrame("Frame", "WarcraftAudiPanel", UIParent, "BackdropTemplate")
A.panel = panel
panel:SetSize(410, 150)
panel:SetPoint("CENTER", UIParent, "CENTER", 0, -240)
panel:SetFrameStrata("DIALOG")
panel:SetClampedToScreen(true)
panel:SetMovable(true)
panel:EnableMouse(true)
panel:RegisterForDrag("LeftButton")
panel:SetScript("OnDragStart", panel.StartMoving)
panel:SetScript("OnDragStop", panel.StopMovingOrSizing)
panel:SetBackdrop({ bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", tile = true, tileSize = 16, edgeSize = 16, insets = { left = 4, right = 4, top = 4, bottom = 4 } })
panel:SetBackdropBorderColor(0.72, 0.53, 0.24)
panel:Hide()

local heading = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
heading:SetPoint("TOPLEFT", 14, -13)
heading:SetText("Warcraft Audi")
local title = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
title:SetPoint("TOPLEFT", 14, -37)
title:SetWidth(380)
title:SetJustifyH("LEFT")
local status = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
status:SetPoint("TOPLEFT", 14, -70)
status:SetWidth(380)
status:SetJustifyH("LEFT")

local function button(parent, label, width, x, callback)
    local result = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    result:SetSize(width, 24)
    result:SetPoint("BOTTOMLEFT", x, 12)
    result:SetText(label)
    result:SetScript("OnClick", callback)
    return result
end
local play = button(panel, "Повтор", 76, 12, function() A:Play() end)
button(panel, "Стоп", 65, 91, function() A:Stop(); A.status = "Остановлено"; A:Refresh() end)
local auto = button(panel, "Авто: вкл", 110, 159, function() A:ToggleAuto() end)
button(panel, "Экспорт", 112, 272, function() A:ShowExport() end)
local close = CreateFrame("Button", nil, panel, "UIPanelCloseButton")
close:SetPoint("TOPRIGHT", -1, -1)
close:SetScript("OnClick", function() A:Stop(); panel:Hide() end)

function A:UpdateUI()
    local names = { detail = "Выдача", progress = "Прогресс", complete = "Завершение" }
    if self.current then
        title:SetText(self.current.title .. " · " .. (names[self.current.stage] or "") .. "\n" .. self.current.speaker)
    else title:SetText("Откройте диалог квеста у NPC.") end
    status:SetText(self.status or "Подключите голосовой пакет для воспроизведения.")
    play:SetEnabled(self:GetVoice() ~= nil)
    auto:SetText("Авто: " .. (self.db and self.db.settings.autoPlay and "вкл" or "выкл"))
end

function A:ShowExport()
    if not self.exportPanel then
        local window = CreateFrame("Frame", "WarcraftAudiExport", UIParent, "BasicFrameTemplateWithInset")
        self.exportPanel = window
        window:SetSize(650, 440)
        window:SetPoint("CENTER")
        window:SetFrameStrata("FULLSCREEN_DIALOG")
        window:SetClampedToScreen(true)
        window.TitleText:SetText("Тексты квестов — Ctrl+A, Ctrl+C")
        local scroll = CreateFrame("ScrollFrame", nil, window, "UIPanelScrollFrameTemplate")
        scroll:SetPoint("TOPLEFT", 16, -38)
        scroll:SetPoint("BOTTOMRIGHT", -32, 18)
        local edit = CreateFrame("EditBox", nil, scroll)
        edit:SetMultiLine(true)
        edit:SetFontObject(ChatFontNormal)
        edit:SetWidth(590)
        edit:SetAutoFocus(false)
        edit:SetMaxLetters(0)
        edit:SetScript("OnEscapePressed", function() window:Hide() end)
        scroll:SetScrollChild(edit)
        window.edit = edit
        table.insert(UISpecialFrames, "WarcraftAudiExport")
    end
    self.exportPanel:Show()
    self.exportPanel.edit:SetText(self:ExportJSON())
    self.exportPanel.edit:SetFocus()
    self.exportPanel.edit:HighlightText()
end
