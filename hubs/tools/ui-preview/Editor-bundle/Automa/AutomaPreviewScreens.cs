#nullable enable
using System;
using System.Collections.Generic;
using Game.Data.ExternalModule.LocalizationModule;
using Game.Data.Module.AutomaModule.Model;
using Game.Data.Module.StatModule.Model;
using Game.SceneView.Module.Modal.Module.AutomaDailySummaryDialog;
using Game.SceneView.Module.Modal.Module.AutomaGoalIntroDialog;
using Game.SceneView.Module.Modal.Module.AutomaInfoDialog;
using Game.SceneView.Module.Modal.Module.AutomaMissionPopupDialog;
using Game.SceneView.Module.Modal.Module.AutomaNoticeDialog;
using Game.SceneView.Module.Modal.Module.AutomaResultDialog;
using Game.SceneView.Scene.InGame.Component.Automa;
using UnityEngine;

namespace Tests.Editor.UiPreview.Automa
{
    /// <summary>
    ///     PR 묶음 브랜치(오토마 UI 시안 반영) 전용 화면 등록. 예고·결과·새 목표 소개·미션 배너가 dev 에 올라오면
    ///     Editor/Automa 기본 목록으로 옮기고 이 파일은 지움.
    /// </summary>
    internal static class AutomaPreviewScreens
    {
        private const string NoMissionReason = "이 스테이지 첫날 발급 미션 없음. --stage 로 미션이 있는 스테이지 지정";

        public static IEnumerable<IUiPreviewScreen> Create(Func<AutomaPreviewFixture> fixture)
        {
            yield return new UiPreviewScreen("automa-notice-rumor", root => RenderNotice(root, fixture, AutomaMissionKind.Rumor));
            yield return new UiPreviewScreen("automa-notice-chance", root => RenderNotice(root, fixture, AutomaMissionKind.Chance));
            yield return new UiPreviewScreen("automa-outcome-chance-success", root => RenderOutcome(root, fixture, AutomaMissionKind.Chance, true));
            yield return new UiPreviewScreen("automa-outcome-rumor-fail", root => RenderOutcome(root, fixture, AutomaMissionKind.Rumor, false));
            yield return new UiPreviewScreen("automa-outcome-rumor-success", root => RenderOutcome(root, fixture, AutomaMissionKind.Rumor, true));
            yield return new UiPreviewScreen("automa-goal-intro", root =>
            {
                var presenter = new AutomaGoalIntroPresenter(LocalizationManager.GetText, stat => stat.GetName());
                var dialog = UiPreviewPrefabs.Spawn<AutomaGoalIntroDialog>(nameof(AutomaGoalIntroDialog), root, true);
                var goal = fixture().GoalIntro;
                var data = new AutomaGoalIntroDialogData(presenter.Build(goal, fixture().Result.Parts));
                UiPreviewDialogs.Init(dialog, () => dialog.InitModal(data, null), goal.Rival.CharacterId);
                dialog.OnModalDisplay();
            });
            yield return new UiPreviewScreen("automa-daily-summary", root =>
            {
                var dialog = UiPreviewPrefabs.Spawn<AutomaDailySummaryDialog>(nameof(AutomaDailySummaryDialog), root, true);
                UiPreviewDialogs.Init(dialog, () => dialog.InitModal(fixture().DailySummary, null), fixture().DailySummary.Rival.CharacterId);
                dialog.OnModalDisplay();
            });
            yield return new UiPreviewScreen("automa-final-result", root =>
            {
                var input = fixture().Result;
                var presenter = new AutomaResultPresenter(LocalizationManager.GetText, stat => stat.GetTitle());
                var dialog = UiPreviewPrefabs.Spawn<AutomaResultDialog>(nameof(AutomaResultDialog), root, true);
                var data = new AutomaResultDialogData(presenter.Build(input), input.Parts);
                UiPreviewDialogs.Init(dialog, () => dialog.InitModal(data, null), input.Rival.CharacterId);
                dialog.OnModalDisplay();
            });
            yield return new UiPreviewScreen("automa-mission-card", root =>
            {
                var presenter = new AutomaMissionCardPresenter(LocalizationManager.GetText, stat => stat.GetName());
                var view = UiPreviewPrefabs.Spawn<AutomaMissionCardView>("AutomaMissionCard", root, false);
                view.Render(presenter.Build(Card(fixture)));
            });
            yield return new UiPreviewScreen("automa-mission-banner", root => RenderBanner(root, fixture, null));
            yield return new UiPreviewScreen("automa-mission-banner-success", root => RenderBanner(root, fixture, AutomaMissionOutcome.Success));
        }

        private static AutomaMissionCardInput Card(Func<AutomaPreviewFixture> fixture)
        {
            return fixture().MissionCard ?? throw new InvalidOperationException(NoMissionReason);
        }

        private static void RenderBanner(Transform root, Func<AutomaPreviewFixture> fixture, AutomaMissionOutcome? outcome)
        {
            var card = Card(fixture);
            var input = new AutomaMissionCardInput(card.Mission, card.Effects, card.Rival, card.Stat, card.Target,
                outcome.HasValue ? card.Target : card.Progress, card.TicksLeft, outcome, card.PartsAcquired, card.PartsTotal);
            var presenter = new AutomaMissionBannerPresenter(LocalizationManager.GetText, stat => stat.GetName());
            var view = UiPreviewPrefabs.Spawn<AutomaMissionBannerView>("AutomaMissionBanner", root, false);
            view.Render(presenter.Build(input));
        }

        // 종류만 바꾸고 이름·스탯·목표·상대는 픽스처 첫 미션 값
        private static void RenderNotice(Transform root, Func<AutomaPreviewFixture> fixture, AutomaMissionKind kind)
        {
            var card = Card(fixture);
            var presenter = new AutomaNoticePresenter(LocalizationManager.GetText, type => type.GetName());
            var input = new AutomaMissionNoticeInput(kind, card.Mission.NameKey, card.Stat, card.Target, card.TicksLeft);
            var dialog = UiPreviewPrefabs.Spawn<AutomaNoticeDialog>(nameof(AutomaNoticeDialog), root, true);
            UiPreviewDialogs.Init(dialog, () => dialog.InitModal(new AutomaNoticeDialogData(presenter.Build(input)), null));
            dialog.OnModalDisplay();
        }

        // ModalController_Automa.RequestAutomaMissionResultDialog 와 같은 조합. 진행값은 성공이면 목표, 실패면 0
        private static void RenderOutcome(Transform root, Func<AutomaPreviewFixture> fixture, AutomaMissionKind kind, bool success)
        {
            var card = Card(fixture);
            var mission = card.Mission;
            var line = Text(success ? mission.SuccessLineKey : mission.FailLineKey);
            var narration = Text(success ? mission.SuccessNarrationKey : mission.FailNarrationKey);
            var state = new AutomaMissionOutcomeViewState(kind, success, LocalizationManager.GetText(mission.NameKey), card.Stat,
                card.Target, success ? card.Target : 0, line, narration, card.Rival.CharacterId);
            var dialog = UiPreviewPrefabs.Spawn<AutomaMissionOutcomeDialog>(nameof(AutomaMissionOutcomeDialog), root, true);
            UiPreviewDialogs.Init(dialog, () => dialog.InitModal(new AutomaMissionOutcomeDialogData(state), null), card.Rival.CharacterId);
            dialog.OnModalDisplay();
        }

        private static string? Text(string? key) => string.IsNullOrEmpty(key) ? null : LocalizationManager.GetText(key);
    }
}
