#nullable enable
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Cysharp.Threading.Tasks;
using Game.Data.ExternalModule.LocalizationModule;
using Game.Data.Module.StatModule.Model;
using Newtonsoft.Json;
using Tests.Editor.UiPreview.Automa;
using UnityEditor;
using UnityEngine;

namespace Tests.Editor.UiPreview
{
    /// <summary>
    ///     UI 화면 단독 미리보기 배치 진입점(-executeMethod). 부팅·로그인·네트워크·플레이 모드 없이 에디터 모드에서
    ///     화면 프리팹 하나를 띄워 해상도마다 연출 시작·절반·끝 3장을 PNG 로 남김.
    ///     실행은 Tools/verify/ui-preview.sh 가 맡음.
    /// </summary>
    public static class UiPreviewEntry
    {
        private const string ScreenArg = "-uiPreviewScreen";
        private const string SizesArg = "-uiPreviewSizes";
        private const string OutArg = "-uiPreviewOut";
        private const string StageArg = "-uiPreviewStage";
        private const string AllScreens = "all";
        private const string DefaultSize = "1080x1920";
        private const string SummaryFile = "summary.json";
        private const string Locale = "ko";
        private const char ListSeparator = ',';
        private const char SizeSeparator = 'x';
        private const float Half = 0.5f;
        private const int SizeParts = 2;

        private const int ExitOk = 0;
        private const int ExitFailed = 1;
        private const int ExitUsage = 2;

        private static readonly string[] Phases = { "t0", "t1", "t2" };

        public static void Run()
        {
            var code = ExitFailed;
            try
            {
                code = Execute(Environment.GetCommandLineArgs());
            }
            catch (Exception e)
            {
                Debug.LogError($"[UiPreview] 실행 실패: {e}");
            }
            finally
            {
                EditorApplication.Exit(code);
            }
        }

        private static int Execute(string[] args)
        {
            var outDir = Arg(args, OutArg) ?? Path.Combine("TestResults", "ui-preview");
            var stageId = Arg(args, StageArg);
            var fixture = new Lazy<AutomaPreviewFixture>(() => AutomaPreviewFixture.Load(stageId));
            var registry = AutomaPreviewScreens.Create(() => fixture.Value).ToList();
            var names = (Arg(args, ScreenArg) ?? string.Empty).Split(ListSeparator, StringSplitOptions.RemoveEmptyEntries);
            var chosen = names.Contains(AllScreens) ? registry : registry.Where(screen => names.Contains(screen.Name)).ToList();
            if (names.Length == 0 || (!names.Contains(AllScreens) && chosen.Count != names.Length))
            {
                Debug.LogError($"[UiPreview] 화면 이름 확인. 가능: {AllScreens}, {string.Join(", ", registry.Select(screen => screen.Name))}");
                return ExitUsage;
            }

            if (!TryParseSizes(Arg(args, SizesArg) ?? DefaultSize, out var sizes))
            {
                Debug.LogError($"[UiPreview] 해상도는 가로x세로 목록(예: {DefaultSize},1440x3120)");
                return ExitUsage;
            }

            EditorLocalizationTable.Load(Locale);
            LoadStatDefinitions();
            var report = new List<ShotReport>();
            using (var stage = UiPreviewStage.Open())
            {
                foreach (var screen in chosen)
                {
                    foreach (var (width, height) in sizes)
                    {
                        report.Add(Capture(stage, screen, width, height, outDir));
                    }
                }
            }

            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, SummaryFile), JsonConvert.SerializeObject(report, Formatting.Indented));
            var failed = report.Count(entry => entry.Error != null);
            Debug.Log($"[UiPreview] 화면 {chosen.Count}개 × 해상도 {sizes.Count}개, 실패 {failed} → {outDir}");
            return failed == 0 ? ExitOk : ExitFailed;
        }

        private static ShotReport Capture(UiPreviewStage stage, IUiPreviewScreen screen, int width, int height, string outDir)
        {
            var entry = new ShotReport { Screen = screen.Name, Width = width, Height = height, Missing = screen.MissingReason };
            if (screen.MissingReason != null)
            {
                return entry;
            }

            var motion = new UiPreviewMotion();
            try
            {
                stage.Resize(width, height);
                screen.Show(stage.Root);
                var half = motion.Span() * Half;
                entry.Shots.Add(Save(stage, outDir, screen.Name, width, height, Phases[0]));
                motion.Advance(half);
                entry.Shots.Add(Save(stage, outDir, screen.Name, width, height, Phases[1]));
                motion.Finish();
                entry.Shots.Add(Save(stage, outDir, screen.Name, width, height, Phases[2]));
            }
            catch (Exception e)
            {
                entry.Error = e.ToString();
                Debug.LogError($"[UiPreview] {screen.Name} {width}x{height} 캡처 실패: {e}");
            }
            finally
            {
                motion.Reset();
                stage.Clear();
            }

            return entry;
        }

        private static string Save(UiPreviewStage stage, string outDir, string name, int width, int height, string phase)
        {
            var path = Path.Combine(outDir, $"{name}_{width}x{height}_{phase}.png");
            stage.Save(path);
            return path;
        }

        // 스탯 칩 색은 StatDefinitionsLoader 표에서 읽음. 부팅이 없어 직접 불러야 하고, 로컬 저장소면 동기로 끝남
        private static void LoadStatDefinitions()
        {
            try
            {
                var task = StatDefinitionsLoader.LoadAsync();
                if (task.Status != UniTaskStatus.Succeeded)
                {
                    Debug.LogWarning($"[UiPreview] 스탯 정의 로드 미완료({task.Status}). 스탯 칩이 프리팹 기본색으로 찍힘");
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[UiPreview] 스탯 정의 로드 실패. 스탯 칩이 프리팹 기본색으로 찍힘: {e.Message}");
            }
        }

        private static bool TryParseSizes(string text, out List<(int, int)> sizes)
        {
            sizes = new List<(int, int)>();
            foreach (var item in text.Split(ListSeparator, StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = item.Split(SizeSeparator);
                if (parts.Length != SizeParts
                    || !int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out var width)
                    || !int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out var height)
                    || width <= 0 || height <= 0)
                {
                    return false;
                }

                sizes.Add((width, height));
            }

            return sizes.Count > 0;
        }

        private static string? Arg(string[] args, string key)
        {
            var index = Array.IndexOf(args, key);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }

        private sealed class ShotReport
        {
            public string Screen { get; set; } = "";
            public int Width { get; set; }
            public int Height { get; set; }
            public List<string> Shots { get; } = new();
            public string? Missing { get; set; }
            public string? Error { get; set; }
        }
    }
}
