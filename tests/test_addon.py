import json
from pathlib import Path
import unittest

try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, "Install lupa==2.6 to execute Lua 5.1 addon tests")
class AddonTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / "tests" / "wow_mock.lua").read_text())
        self.lua.execute("namespace = {}")
        self.load_addon()

    def load_addon(self):
        for file in ("Core.lua", "UI.lua", "Bridge.lua"):
            code = (ROOT / "addon" / "WarcraftAudi" / file).read_text()
            self.lua.execute("return function(...)\n" + code + "\nend")("WarcraftAudi", self.lua.globals().namespace)
        self.lua.execute('fire("ADDON_LOADED", "WarcraftAudi")')

    def run_lua(self, code):
        self.lua.execute(code)

    def register_voice(self):
        self.run_lua('assert(WarcraftAudi:RegisterPack("Test", {{questID=42, locale="ruRU", stage="detail", files={"a.ogg", "b.ogg"}}}))')

    def test_collect_all_three_stages_and_valid_unicode_export(self):
        self.run_lua('mock.text = "|cffffffffПривет|r, \\\"герой\\\"!\\n\\tПуть: C:\\\\test"; fire("QUEST_DETAIL"); fire("QUEST_PROGRESS"); fire("QUEST_COMPLETE")')
        export = json.loads(self.lua.eval("WarcraftAudi:ExportJSON()"))
        rows = {row["stage"]: row for row in export["quests"]}
        self.assertEqual(set(rows), {"detail", "progress", "complete"})
        self.assertEqual(rows["detail"]["text"], 'Привет, "герой"!\n\tПуть: C:\\test')
        self.assertEqual(rows["detail"]["npc_id"], 123)
        self.assertEqual(rows["complete"]["text"], "Отличная работа!")
        self.assertEqual(rows["progress"]["audio"], [])

    def test_queue_finishes_without_overlap(self):
        self.register_voice()
        self.run_lua('fire("QUEST_DETAIL"); assert(#mock.calls == 1); assert(mock.calls[1].channel == "Dialog"); tick(0.5); assert(#mock.calls == 1); mock.playing[1] = false; tick(0.3); assert(#mock.calls == 2); assert(mock.calls[2].path == "b.ogg"); mock.playing[2] = false; tick(0.3); assert(WarcraftAudi.queue == nil); assert(WarcraftAudi.soundHandle == nil)')

    def test_repeat_stop_and_close_only_stop_owned_audio(self):
        self.register_voice()
        self.run_lua('fire("QUEST_DETAIL"); WarcraftAudi:Play(); assert(mock.stopped[1] == 1); assert(#mock.calls == 2); fire("QUEST_FINISHED"); assert(mock.stopped[2] == 2); tick(1); assert(#mock.calls == 2); assert(WarcraftAudi.current == nil); assert(not WarcraftAudi.panel.visible)')

    def test_stage_transition_stops_previous_clip(self):
        self.register_voice()
        self.run_lua('fire("QUEST_DETAIL"); fire("QUEST_PROGRESS"); assert(mock.stopped[1] == 1); assert(WarcraftAudi.soundHandle == nil); tick(1); assert(#mock.calls == 1); assert(WarcraftAudi.current.stage == "progress")')

    def test_missing_recording_locale_and_failed_playback(self):
        self.register_voice()
        self.run_lua('mock.locale = "enUS"; fire("QUEST_DETAIL"); assert(#mock.calls == 0); mock.locale = "ruRU"; mock.failPlay = true; fire("QUEST_DETAIL"); assert(#mock.calls == 1); assert(WarcraftAudi.queue == nil); assert(WarcraftAudi.soundHandle == nil)')

    def test_toggle_auto_manual_play_and_collection(self):
        self.register_voice()
        self.run_lua('WarcraftAudi:ToggleAuto(); fire("QUEST_DETAIL"); assert(#mock.calls == 0); WarcraftAudi:Play(); assert(#mock.calls == 1); SlashCmdList.WARCRAFTAUDI("collect"); mock.id = 43; fire("QUEST_DETAIL"); assert(WarcraftAudiDB.collected["ruRU:43:detail"] == nil)')

    def test_saved_settings_survive_reloading_addon(self):
        self.run_lua('WarcraftAudi:ToggleAuto(); fire("QUEST_DETAIL")')
        self.load_addon()
        self.run_lua('assert(WarcraftAudiDB.settings.autoPlay == false); assert(WarcraftAudiDB.collected["ruRU:42:detail"] ~= nil)')

    def test_pack_registration_atomic_on_conflict(self):
        self.register_voice()
        self.run_lua('local ok = WarcraftAudi:RegisterPack("Conflict", {{questID=43, locale="ruRU", stage="detail", files={"c.ogg"}}, {questID=42, locale="ruRU", stage="detail", files={"x.ogg"}}}); assert(not ok); assert(WarcraftAudi.voices["ruRU:43:detail"] == nil); assert(WarcraftAudi.voices["ruRU:42:detail"].pack == "Test")')

    def test_no_npc_export_ui_and_empty_export(self):
        self.assertEqual(json.loads(self.lua.eval("WarcraftAudi:ExportJSON()"))["quests"], [])
        self.run_lua('mock.noNPC = true; fire("QUEST_DETAIL"); SlashCmdList.WARCRAFTAUDI("export"); assert(WarcraftAudi.exportPanel.visible); assert(WarcraftAudi.exportPanel.edit.text == WarcraftAudi:ExportJSON())')
        self.assertNotIn("npc_id", json.loads(self.lua.eval("WarcraftAudi:ExportJSON()"))["quests"][0])

    def test_demo_can_play_without_opening_a_quest(self):
        self.run_lua('SlashCmdList.WARCRAFTAUDI("demo"); assert(#mock.calls == 1); assert(mock.calls[1].path == "Interface\\\\AddOns\\\\WarcraftAudi\\\\Audio\\\\demo.ogg"); assert(WarcraftAudi.panel.visible); SlashCmdList.WARCRAFTAUDI("stop"); assert(mock.stopped[1] == 1); tick(1); assert(#mock.calls == 1)')

    def test_gender_specific_voice_selection_and_generic_fallback(self):
        self.register_voice()
        self.run_lua('assert(WarcraftAudi:RegisterPack("Female", {{questID=42, locale="ruRU", stage="detail", gender="female", files={"female.ogg"}}})); mock.sex = 3; fire("QUEST_DETAIL"); assert(mock.calls[1].path == "female.ogg"); mock.sex = 2; fire("QUEST_DETAIL"); assert(mock.calls[2].path == "a.ogg")')

    def test_selected_log_quest_can_be_read_without_an_npc(self):
        self.run_lua('C_QuestLog = {GetSelectedQuest=function() return 77 end, GetTitleForQuestID=function() return "Журнал" end}; GetQuestLogQuestText=function() return "Текст из журнала" end; SlashCmdList.WARCRAFTAUDI("companion"); SlashCmdList.WARCRAFTAUDI("read"); assert(WarcraftAudi.current.quest_id == 77); assert(WarcraftAudi.current.npc_id == 0); assert(#mock.calls == 0)')
        payload = json.loads(self.lua.eval('WarcraftAudi.bridge.message'))
        self.assertEqual(payload['text'], 'Текст из журнала')
        self.assertEqual(payload['speaker'], 'Рассказчик')

    def test_delayed_quest_log_load_never_reads_a_different_selection(self):
        self.run_lua('mock.selected = 77; C_QuestLog = {GetSelectedQuest=function() return mock.selected end, GetTitleForQuestID=function() return "Журнал" end, RequestLoadQuestByID=function(id) mock.requested=id end}; GetQuestLogQuestText=function() return mock.loaded and "Готовый текст" or "" end; SlashCmdList.WARCRAFTAUDI("companion"); SlashCmdList.WARCRAFTAUDI("read"); assert(mock.requested == 77); mock.selected=88; mock.loaded=true; fire("QUEST_DATA_LOAD_RESULT",77,true); assert(WarcraftAudi.current == nil); SlashCmdList.WARCRAFTAUDI("read"); assert(WarcraftAudi.current.quest_id == 88)')


if __name__ == "__main__":
    unittest.main()
