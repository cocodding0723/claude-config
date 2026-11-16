#nullable enable
using System;
using UnityEditor;
using UnityEngine;

namespace Tests.Editor.UiPreview
{
    /// <summary>미리보기 화면 하나. 프리팹을 캔버스 아래 띄우고 픽스처 값으로 그린 뒤 연출 시작까지 맡음.</summary>
    internal interface IUiPreviewScreen
    {
        string Name { get; }

        /// <summary>샷을 못 찍는 이유. 찍을 수 있으면 null.</summary>
        string? MissingReason { get; }

        void Show(Transform root);
    }

    internal sealed class UiPreviewScreen : IUiPreviewScreen
    {
        private readonly Action<Transform> _show;

        public UiPreviewScreen(string name, Action<Transform> show)
        {
            Name = name;
            _show = show;
        }

        public string Name { get; }
        public string? MissingReason => null;

        public void Show(Transform root) => _show(root);
    }

    /// <summary>dev 에 프리팹이 아직 없는 화면. 샷 대신 이유만 남김.</summary>
    internal sealed class MissingPreviewScreen : IUiPreviewScreen
    {
        public MissingPreviewScreen(string name, string reason)
        {
            Name = name;
            MissingReason = reason;
        }

        public string Name { get; }
        public string? MissingReason { get; }

        public void Show(Transform root) => throw new InvalidOperationException(MissingReason);
    }

    internal static class UiPreviewPrefabs
    {
        /// <summary>이름으로 찾은 프리팹 중 루트에 <typeparamref name="T"/> 가 있는 것을 띄움.</summary>
        /// <param name="fill">true 면 화면 가득(모달), false 면 원래 크기로 가운데(HUD 부품).</param>
        /// <exception cref="InvalidOperationException">해당 프리팹 없음.</exception>
        public static T Spawn<T>(string prefabName, Transform root, bool fill) where T : Component
        {
            foreach (var guid in AssetDatabase.FindAssets($"t:Prefab {prefabName}"))
            {
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(guid));
                if (asset == null || asset.name != prefabName || asset.GetComponent<T>() == null)
                {
                    continue;
                }

                var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset, root);
                if (instance.transform is RectTransform rect)
                {
                    Place(rect, fill);
                }

                return instance.GetComponent<T>();
            }

            throw new InvalidOperationException($"프리팹 {prefabName}({typeof(T).Name}) 없음");
        }

        private static void Place(RectTransform rect, bool fill)
        {
            if (fill)
            {
                rect.anchorMin = Vector2.zero;
                rect.anchorMax = Vector2.one;
                rect.offsetMin = Vector2.zero;
                rect.offsetMax = Vector2.zero;
                return;
            }

            var size = rect.rect.size;
            var center = Vector2.one * 0.5f;
            rect.anchorMin = center;
            rect.anchorMax = center;
            rect.pivot = center;
            rect.sizeDelta = size;
            rect.anchoredPosition = Vector2.zero;
        }
    }
}
