#nullable enable
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Game.Data.ExternalModule.RemoteGameDataModule;
using Game.Data.Module.AutomaModule.Model;
using Game.Data.Module.AutomaModule.Record;
using Game.Data.Module.AutomaModule.Run;
using Game.Data.Module.AutomaModule.Table;
using Game.Data.Module.ConfigModule;
using Game.Data.Module.StatModule.Model;
using Game.Data.Table;
using Game.SceneView.Module.Modal.Module.AutomaDailySummaryDialog;
using Game.SceneView.Module.Modal.Module.AutomaGoalIntroDialog;
using Game.SceneView.Module.Modal.Module.AutomaResultDialog;
using Game.SceneView.Scene.InGame.Component.Automa;

namespace Tests.Editor.UiPreview.Automa
{
    /// <summary>
    ///     시트 고정본(로컬 게임 데이터 저장소)의 오토마 표로 첫 스테이지 하루를 실제 판 진행으로 돌려 만든 화면 입력.
    ///     기획 값은 코드에 두지 않음 — 플레이어 스탯은 오토마 초기값, 남은 일수는 발생 표의 마지막 날 기준.
    /// </summary>
    internal sealed class AutomaPreviewFixture
    {
        private const string RunId = "ui-preview";
        private const long GrowthSeed = 1;
        private const int FirstDay = 1;

        private AutomaPreviewFixture(AutomaGoalIntroInput goalIntro, AutomaDailySummaryDialogData dailySummary,
            AutomaResultInput result, AutomaMissionCardInput? missionCard)
        {
            GoalIntro = goalIntro;
            DailySummary = dailySummary;
            Result = result;
            MissionCard = missionCard;
        }

        public AutomaGoalIntroInput GoalIntro { get; }
        public AutomaDailySummaryDialogData DailySummary { get; }
        public AutomaResultInput Result { get; }

        /// <summary>하루 중 처음 발급된 미션의 진행 중 카드. 그날 발생 일정이 없으면 null.</summary>
        public AutomaMissionCardInput? MissionCard { get; }

        /// <exception cref="FileNotFoundException">로컬 게임 데이터 저장소 없음.</exception>
        public static AutomaPreviewFixture Load(string? stageId)
        {
            var tables = LoadTables();
            var stage = stageId == null ? tables.Stages.Rows.First() : tables.Stages.ById[stageId];
            var player = new FixturePlayer(tables.RivalStats.Rows.Where(row => row.RivalId == stage.RivalId));
            var ticksPerDay = IngameConfig.MaxRunningTurnsPerDay;
            var run = AutomaRun.Start(tables, stage.Id, RunId, GrowthSeed, ticksPerDay, new List<string>(), player);
            var builder = new AutomaDialogInputBuilder(run, new List<string>());

            var goalIntro = builder.GoalIntro(builder.PlayerStats(player.GetStat));
            var dayStart = player.Snapshot();
            AutomaMissionCardInput? card = null;
            for (var tick = 1; tick <= ticksPerDay; tick++)
            {
                if (run.OnTickStarted(FirstDay, tick) != null && card == null)
                {
                    card = builder.ActiveCard();
                }
            }

            run.OnDayEnded();
            var lastDay = tables.Spawns.Rows.Where(row => row.StageId == stage.Id).Select(row => row.ToDay).DefaultIfEmpty(FirstDay).Max();
            var summary = builder.DailySummary(FirstDay, builder.PlayerStats(player.GetStat, dayStart), lastDay - FirstDay);
            var result = builder.Result(run.Finish(player.GetStat));
            return new AutomaPreviewFixture(goalIntro, summary, result, card);
        }

        private static AutomaTables LoadTables()
        {
            var root = RemoteGameDataPaths.EditorStoreRoot;
            var store = RemoteGameDataStore.Fixed(root);
            if (!store.TryLoad())
            {
                throw new FileNotFoundException($"로컬 게임 데이터 없음: {root}. node Tools/game-data/local/refresh.mjs 실행 후 재시도");
            }

            T Read<T>() where T : ITable, new()
            {
                var address = new T().TableAddress;
                var text = store.TryReadText(address) ?? throw new FileNotFoundException($"로컬 게임 데이터에 없는 표: {address}");
                return TableJson.Parse<T>(text);
            }

            return new AutomaTables(Read<AutomaStageTable>(), Read<AutomaRivalTable>(), Read<AutomaRivalStatTable>(),
                Read<AutomaMissionTable>(), Read<AutomaMissionEffectTable>(), Read<AutomaSpawnTable>(), Read<AutomaPartTable>());
        }

        /// <summary>오토마와 같은 초기 스탯에서 시작하는 플레이어. 미션 효과는 그대로 반영.</summary>
        private sealed class FixturePlayer : IAutomaPlayerPort
        {
            private readonly Dictionary<StatType, int> _stats;
            private int _hp;

            public FixturePlayer(IEnumerable<AutomaRivalStatRow> rivalStats)
            {
                _stats = rivalStats.ToDictionary(row => row.Stat, row => row.Initial);
            }

            public IReadOnlyDictionary<StatType, int> Snapshot() => new Dictionary<StatType, int>(_stats);

            public int GetStat(StatType stat) => _stats.TryGetValue(stat, out var value) ? value : 0;

            public int GetHp() => _hp;

            public void AdjustStat(StatType stat, int amount) => _stats[stat] = GetStat(stat) + amount;

            public void AdjustHp(int amount) => _hp += amount;

            public void AdjustTension(int amount) { }
        }
    }
}
