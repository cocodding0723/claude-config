#nullable enable
using System;
using System.Collections.Generic;
using System.Linq;
using Game.Data.ExternalModule.LocalizationModule;
using Game.Data.Module.AutomaModule.Domain.Mission;
using Game.Data.Module.AutomaModule.Model;
using Game.Data.Module.StatModule.Model;
using Game.SceneView.Module.Modal.Module.AutomaDailySummaryDialog;
using Game.SceneView.Scene.Ingame.Component.Hud;
using Game.Util;
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
    internal static class TransformSearch
    {
        public static Transform? FindDeep(this Transform parent, string name)
        {
            foreach (var child in parent.GetComponentsInChildren<Transform>(true))
            {
                if (child.name == name)
                {
                    return child;
                }
            }

            return null;
        }
    }

    internal static class AutomaPreviewScreens
    {
        private const string LocationLabelName = "Text_LocationName";
        // AutomaMissionBandBinder.RewardIconVariant 와 같은 값(그쪽이 private)
        private const int RewardIconVariant = 2;
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
            yield return new UiPreviewScreen("ingame-top-band", root => RenderTopBand(root, fixture, true));
            yield return new UiPreviewScreen("ingame-top-band-nomission", root => RenderTopBand(root, fixture, false));
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

        // 미션 띠는 게임 Presenter + 고정본 미션. 장소·남은 시간·코인은 고정본에 없어 비움(시안 견본 문구가 남지 않게)
        private static void RenderTopBand(Transform root, Func<AutomaPreviewFixture> fixture, bool withMission)
        {
            var view = UiPreviewPrefabs.Spawn<IngameTopUIElementView>(nameof(IngameTopUIElementView).Replace("View", string.Empty), root, false);
            // 게임에서는 상단 영역 부모가 위치를 정함. 미리보기엔 부모가 없어 화면 위쪽에 붙임
            if (view.transform is RectTransform band)
            {
                band.anchorMin = new Vector2(0f, 1f);
                band.anchorMax = Vector2.one;
                band.pivot = new Vector2(0.5f, 1f);
                band.anchoredPosition = Vector2.zero;
                band.sizeDelta = new Vector2(0f, band.sizeDelta.y);
            }

            foreach (var label in new[] { view.GoldValue, view.LeftTimeValue })
            {
                if (label != null)
                {
                    label.text = string.Empty;
                }
            }

            var location = view.transform.FindDeep(LocationLabelName);
            if (location != null && location.TryGetComponent<TMPro.TMP_Text>(out var locationText))
            {
                locationText.text = string.Empty;
            }

            Debug.Log("[UiPreview] 상단 띠: 장소·남은 시간·코인 데이터 없음(비움)");
            if (!withMission)
            {
                view.MissionBand.Render(MissionBandState.Hidden);
                return;
            }

            var presenter = new AutomaMissionBandPresenter(LocalizationManager.GetText, stat => stat.GetName(),
                stat => AddressKeys.Ui.StatIcon(stat, RewardIconVariant));
            view.MissionBand.Render(presenter.Build(Card(fixture)));
        }

        // 보상 줄 인자가 있는 브랜치(예고 보상 칸)와 없는 묶음 둘 다 컴파일되게 생성자 인자 수로 고름.
        // 보상은 AutomaDialogInputBuilder.MissionNotice 와 같은 조합: 성공 효과 뒤에 실패 효과
        private static AutomaMissionNoticeInput NoticeInput(AutomaMissionKind kind, AutomaMissionCardInput card)
        {
            var rewards = AutomaMissionJudge.EffectsFor(card.Effects, AutomaMissionOutcome.Success, true, card.Stat)
                .Concat(AutomaMissionJudge.EffectsFor(card.Effects, AutomaMissionOutcome.Fail, true, card.Stat))
                .ToList();
            var args = new List<object> { kind, card.Mission.NameKey, card.Stat, card.Target, card.TicksLeft, rewards };
            foreach (var ctor in typeof(AutomaMissionNoticeInput).GetConstructors())
            {
                var count = ctor.GetParameters().Length;
                if (count <= args.Count)
                {
                    return (AutomaMissionNoticeInput)ctor.Invoke(args.GetRange(0, count).ToArray());
                }
            }

            throw new InvalidOperationException("AutomaMissionNoticeInput 생성자 형식이 바뀜");
        }

        // 종류만 바꾸고 이름·스탯·목표·상대는 픽스처 첫 미션 값
        private static void RenderNotice(Transform root, Func<AutomaPreviewFixture> fixture, AutomaMissionKind kind)
        {
            var card = Card(fixture);
            var presenter = new AutomaNoticePresenter(LocalizationManager.GetText, type => type.GetName());
            var input = NoticeInput(kind, card);
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
                card.Target, success ? card.Target : 0, line, narration, card.Rival.CharacterId, Effects(card));
            var dialog = UiPreviewPrefabs.Spawn<AutomaMissionOutcomeDialog>(nameof(AutomaMissionOutcomeDialog), root, true);
            UiPreviewDialogs.Init(dialog, () => dialog.InitModal(new AutomaMissionOutcomeDialogData(state), null), card.Rival.CharacterId);
            dialog.OnModalDisplay();
        }

        // 게임은 판정 결과(AutomaMissionResolution.Effects)를 넘김. 미리보기는 판정이 없어 미션 효과 표 행을 그대로 옮김
        private static IReadOnlyList<AutomaEffect> Effects(AutomaMissionCardInput card)
        {
            var effects = new List<AutomaEffect>();
            foreach (var row in card.Effects)
            {
                effects.Add(new AutomaEffect(row.Target, row.Kind, row.Stat, row.Amount, row.When));
            }

            return effects;
        }

        private static string? Text(string? key) => string.IsNullOrEmpty(key) ? null : LocalizationManager.GetText(key);
    }
}
