#nullable enable
using System;
using System.Collections.Generic;
using Game.Data.ExternalModule.LocalizationModule;
using Game.Data.Module.StatModule.Model;
using Game.SceneView.Module.Modal.Module.AutomaDailySummaryDialog;
using Game.SceneView.Module.Modal.Module.AutomaInfoDialog;
using Game.SceneView.Module.Modal.Module.AutomaResultDialog;
using Game.SceneView.Scene.InGame.Component.Automa;
using UnityEngine;

namespace Tests.Editor.UiPreview.Automa
{
    /// <summary>오토마 화면 등록. 문구·스탯 이름 주입은 ModalController_Automa 와 같은 조합.</summary>
    internal static class AutomaPreviewScreens
    {
        private const string MissingPrefab = "dev 에 프리팹 없음";

        public static IEnumerable<IUiPreviewScreen> Create(Func<AutomaPreviewFixture> fixture)
        {
            yield return new MissingPreviewScreen("automa-notice", $"{MissingPrefab}(AutomaNoticeDialog)");
            yield return new MissingPreviewScreen("automa-outcome", $"{MissingPrefab}(AutomaMissionOutcomeDialog)");
            yield return new UiPreviewScreen("automa-goal-intro", root =>
            {
                // 새 목표 소개 팝업(AutomaGoalIntroDialog)이 dev 에 없어 지금 연결된 정보창으로 표시
                var presenter = new AutomaGoalIntroPresenter(LocalizationManager.GetText, stat => stat.GetName());
                var dialog = UiPreviewPrefabs.Spawn<AutomaInfoDialog>(nameof(AutomaInfoDialog), root, true);
                var data = new AutomaInfoDialogData(presenter.Build(fixture().GoalIntro));
                UiPreviewDialogs.Init(dialog, () => dialog.InitModal(data, null));
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
                var input = fixture().MissionCard
                    ?? throw new InvalidOperationException("첫 스테이지 첫날 발급 미션 없음. --stage 로 미션이 있는 스테이지 지정");
                var presenter = new AutomaMissionCardPresenter(LocalizationManager.GetText, stat => stat.GetName());
                var view = UiPreviewPrefabs.Spawn<AutomaMissionCardView>("AutomaMissionCard", root, false);
                view.Render(presenter.Build(input));
            });
        }
    }
}
