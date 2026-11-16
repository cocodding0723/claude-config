using System;
using System.Collections.Generic;
using DG.Tweening;
using UnityEngine;

namespace Tests.Editor.UiPreview
{
    /// <summary>
    ///     트윈 시간 조종. 에디터 모드에는 DOTween 자동 갱신이 없어 진행 중 트윈을 Manual 갱신으로 옮겨 직접 진행.
    ///     시작·절반·끝 3장을 찍게 시점을 나눔.
    /// </summary>
    internal sealed class UiPreviewMotion
    {
        private const float StepSeconds = 1f / 60f;

        // 콜백이 새 트윈을 계속 잇는 경우의 상한. 넘으면 끝 장면이 덜 끝난 채로 찍힘
        private const int MaxCompletePasses = 32;

        private readonly List<Tween> _buffer = new();

        /// <summary>지금 진행 중인 트윈의 가장 긴 총 길이(지연 포함, 무한 반복은 한 바퀴).</summary>
        public float Span()
        {
            var longest = 0f;
            foreach (var tween in Playing())
            {
                var loop = tween.Duration(false);
                var total = tween.Loops() < 0 ? loop : tween.Duration();
                longest = Mathf.Max(longest, tween.Delay() + total);
            }

            return longest;
        }

        public void Advance(float seconds)
        {
            var left = seconds;
            while (left > 0f)
            {
                var step = Mathf.Min(StepSeconds, left);
                TakeOver();
                DOTween.ManualUpdate(step, step);
                left -= step;
            }
        }

        public void Finish()
        {
            for (var pass = 0; pass < MaxCompletePasses && Playing().Count > 0; pass++)
            {
                DOTween.CompleteAll(true);
                TakeOver();
                DOTween.ManualUpdate(StepSeconds, StepSeconds);
            }
        }

        public void Reset()
        {
            DOTween.KillAll();
        }

        // 콜백이 연 새 트윈도 Normal 로 생기므로 매 단계 다시 옮김
        private void TakeOver()
        {
            foreach (var tween in Playing())
            {
                if (tween.IsActive() && !tween.IsComplete())
                {
                    tween.SetUpdate(UpdateType.Manual, true);
                }
            }
        }

        private IReadOnlyList<Tween> Playing()
        {
            _buffer.Clear();
            var playing = DOTween.PlayingTweens(_buffer);
            return (IReadOnlyList<Tween>)playing ?? Array.Empty<Tween>();
        }
    }
}
