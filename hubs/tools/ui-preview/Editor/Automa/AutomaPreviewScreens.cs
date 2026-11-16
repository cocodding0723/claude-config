#nullable enable
using System;
using System.Collections.Generic;
using Game.Data.ExternalModule.LocalizationModule;
using Game.Data.Module.StatModule.Model;
using Game.SceneView.Module.Modal.Module.AutomaDailySummaryDialog;
using Game.Data.Module.AutomaModule.Model;
using Game.SceneView.Module.Modal.Module.AutomaInfoDialog;
using Game.SceneView.Module.Modal.Module.AutomaNoticeDialog;
using Game.SceneView.Module.Modal.Module.AutomaResultDialog;
using Game.SceneView.Scene.InGame.Component.Automa;
using UnityEngine;
using UnityEngine.UI;

namespace Tests.Editor.UiPreview.Automa
{
    /// <summary>오토마 화면 등록. 문구·스탯 이름 주입은 ModalController_Automa 와 같은 조합.</summary>
    internal static class AutomaPreviewScreens
    {
        private const string MissingPrefab = "dev 에 프리팹 없음";

        public static IEnumerable<IUiPreviewScreen> Create(Func<AutomaPreviewFixture> fixture)
        {
            yield return new UiPreviewScreen("automa-notice-rumor", root => RenderNotice(root, AutomaMissionKind.Rumor, StatType.Sense));
            yield return new UiPreviewScreen("automa-notice-chance", root => RenderNotice(root, AutomaMissionKind.Chance, StatType.Study));
            yield return new MissingPreviewScreen("automa-outcome", $"{MissingPrefab}(AutomaMissionOutcomeDialog)");
            yield return new UiPreviewScreen("automa-goal-intro", root =>
            {
                // AutomaGoalIntroPsdDialog 가 dev 에 없어 지금 연결된 정보창으로 표시
                var presenter = new AutomaGoalIntroPresenter(LocalizationManager.GetText, stat => stat.GetName());
                var dialog = UiPreviewPrefabs.Spawn<AutomaInfoDialog>(nameof(AutomaInfoDialog), root, true);
                var data = new AutomaInfoDialogData(presenter.Build(fixture().GoalIntro));
                InitLaidOut(dialog, () => dialog.InitModal(data, null));
                dialog.OnModalDisplay();
            });
            yield return new UiPreviewScreen("automa-daily-summary", root =>
            {
                var dialog = UiPreviewPrefabs.Spawn<AutomaDailySummaryDialog>(nameof(AutomaDailySummaryDialog), root, true);
                InitLaidOut(dialog, () => dialog.InitModal(fixture().DailySummary, null));
                dialog.OnModalDisplay();
            });
            yield return new UiPreviewScreen("automa-final-result", root =>
            {
                var input = fixture().Result;
                var presenter = new AutomaResultPresenter(LocalizationManager.GetText, stat => stat.GetTitle());
                var dialog = UiPreviewPrefabs.Spawn<AutomaResultDialog>(nameof(AutomaResultDialog), root, true);
                var data = new AutomaResultDialogData(presenter.Build(input), input.Parts);
                InitLaidOut(dialog, () => dialog.InitModal(data, null));
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

        private const int NoticeTarget = 40;
        private const int NoticeTicksLeft = 4;
        private const int NoticeRewardAmount = 500;

        // 대사·보상 줄은 아직 입력 데이터가 없어 시안 배치 확인용 견본 문구를 넣음
        private static void RenderNotice(Transform root, AutomaMissionKind kind, StatType stat)
        {
            var presenter = new AutomaNoticePresenter(LocalizationManager.GetText, type => type.GetName());
            var input = new AutomaMissionNoticeInput(kind, string.Empty, stat, NoticeTarget, NoticeTicksLeft);
            var amount = NoticeRewardAmount.ToString();

            var state = presenter.Build(input) with
            {
                TalkLine = "(미리보기) 공주영, 넌 공부는 하고 있어?\n나중에 자립이 발목은 잡지 말아야지.",
                BannerLine = "(미리보기) 최한별이 승부를 걸어왔다...",
                RewardRows = new[]
                {
                    new AutomaNoticeRewardRowState("상대", StatType.Study, StatType.Study.GetName(), amount, "감소"),
                    new AutomaNoticeRewardRowState("실패 시 나의", StatType.Study, StatType.Study.GetName(), amount, "감소"),
                },
            };

            var dialog = UiPreviewPrefabs.Spawn<AutomaNoticeDialog>(nameof(AutomaNoticeDialog), root, true);
            InitLaidOut(dialog, () => dialog.InitModal(new AutomaNoticeDialogData(state), null));
            dialog.OnModalDisplay();
        }

        // 연출 Prepare 가 InitModal 안에서 줄 위치를 잡는데, 에디터 모드에선 그 시점에 레이아웃이 안 돌아 0 위치가 잡힘.
        // 한 번 그려 레이아웃을 확정한 뒤 다시 그려 실제 위치로 연출 기준을 맞춤
        private static void InitLaidOut(Component dialog, Action init)
        {
            init();
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)dialog.transform);
            init();
        }
    }
}
