using System;
using System.IO;
using Core.UI;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace Tests.Editor.UiPreview
{
    /// <summary>
    ///     미리보기 씬의 카메라·캔버스. 게임 캔버스는 ScreenSpaceOverlay 라 배치모드에서 화면 캡처가 안 되므로
    ///     씬이 소유한 ScreenSpaceCamera 캔버스를 RenderTexture 카메라로 그려 PNG 로 저장.
    /// </summary>
    internal sealed class UiPreviewStage : IDisposable
    {
        private const int DepthBits = 24;

        private readonly Camera _camera;
        private readonly Canvas _canvas;
        private readonly CanvasScaler _scaler;
        private readonly Vector2 _reference;
        private RenderTexture _target;
        private Texture2D _readback;

        private UiPreviewStage(Camera camera, Canvas canvas, CanvasScaler scaler, Vector2 reference)
        {
            _camera = camera;
            _canvas = canvas;
            _scaler = scaler;
            _reference = reference;
        }

        public Transform Root => _canvas.transform;

        public static UiPreviewStage Open()
        {
            CreateScene();
            var camera = Object.FindAnyObjectByType<Camera>();
            var canvas = Object.FindAnyObjectByType<Canvas>();
            if (camera == null || canvas == null)
            {
                throw new InvalidOperationException("미리보기 씬에 카메라·캔버스 없음");
            }

            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = camera;
            var scaler = canvas.GetComponent<CanvasScaler>() ?? canvas.gameObject.AddComponent<CanvasScaler>();
            return new UiPreviewStage(camera, canvas, scaler, GameReferenceResolution());
        }

        /// <summary>해상도 변경. 배율은 게임 SafeCanvasScaler(Expand)와 같은 min(가로비, 세로비).</summary>
        public void Resize(int width, int height)
        {
            ReleaseTarget();
            _target = new RenderTexture(width, height, DepthBits, RenderTextureFormat.ARGB32);
            _readback = new Texture2D(width, height, TextureFormat.RGBA32, false);
            _camera.targetTexture = _target;

            // 배치 에디터의 Screen 크기는 실제 렌더 대상과 무관해 ScaleWithScreenSize 대신 배율을 직접 지정
            _scaler.uiScaleMode = CanvasScaler.ScaleMode.ConstantPixelSize;
            _scaler.scaleFactor = Mathf.Min(width / _reference.x, height / _reference.y);
            Refresh();
        }

        public void Refresh()
        {
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)_canvas.transform);
            Canvas.ForceUpdateCanvases();
        }

        public void Save(string path)
        {
            Refresh();
            _camera.Render();
            var previous = RenderTexture.active;
            RenderTexture.active = _target;
            _readback.ReadPixels(new Rect(0, 0, _target.width, _target.height), 0, 0);
            _readback.Apply();
            RenderTexture.active = previous;
            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ".");
            File.WriteAllBytes(path, _readback.EncodeToPNG());
        }

        public void Clear()
        {
            var root = _canvas.transform;
            for (var i = root.childCount - 1; i >= 0; i--)
            {
                Object.DestroyImmediate(root.GetChild(i).gameObject);
            }
        }

        public void Dispose()
        {
            Clear();
            ReleaseTarget();
        }

        // 게임 저장소에 씬 파일을 남기지 않게 메모리에만 만듦. 부팅 오브젝트 없이 카메라·캔버스만 둠
        private static void CreateScene()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var camera = new GameObject("PreviewCamera").AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.gray;
            camera.orthographic = true;
            var canvas = new GameObject("PreviewCanvas", typeof(RectTransform)).AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = camera;
            canvas.gameObject.AddComponent<CanvasScaler>();
        }

        // 기준 해상도는 게임 스케일러 기본값을 그대로 읽음(값 복사 금지)
        private static Vector2 GameReferenceResolution()
        {
            var probe = new GameObject(nameof(SafeCanvasScaler));
            probe.SetActive(false);
            try
            {
                return probe.AddComponent<SafeCanvasScaler>().referenceResolution;
            }
            finally
            {
                Object.DestroyImmediate(probe);
            }
        }

        private void ReleaseTarget()
        {
            _camera.targetTexture = null;
            if (_target != null)
            {
                _target.Release();
                Object.DestroyImmediate(_target);
                _target = null;
            }

            if (_readback != null)
            {
                Object.DestroyImmediate(_readback);
                _readback = null;
            }
        }
    }
}
