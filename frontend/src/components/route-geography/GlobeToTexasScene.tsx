import { useEffect, useRef } from "react";
import { mesh } from "topojson-client";
import worldCountries from "world-atlas/countries-110m.json";

import type { RoutePoint } from "./routeCatalog";

type SceneProps = {
  corridor: RoutePoint[];
  isRoadRoute: boolean;
  pickupLabel: string;
  deliveryLabel: string;
  reducedMotion: boolean;
  resetKey: number;
  autoPlay: boolean;
  immediateIntro: boolean;
};

type ThreeModule = typeof import("three");

// U.S. Census Bureau TIGERweb 2025 state boundary, Texas (GEOID 48),
// generalized to 0.075° and bundled for offline use. Public domain.
const TEXAS_OUTLINE: RoutePoint[] = [
  [-98.42, 34.08],
  [-99.19, 34.21],
  [-99.36, 34.46],
  [-99.69, 34.38],
  [-100, 34.56],
  [-100, 36.5],
  [-103.04, 36.5],
  [-103.06, 32],
  [-106.62, 32],
  [-106.65, 31.9],
  [-105.4, 30.85],
  [-104.92, 30.6],
  [-104.51, 29.63],
  [-103.29, 28.98],
  [-103.12, 28.98],
  [-102.87, 29.22],
  [-102.67, 29.74],
  [-102.39, 29.76],
  [-102.32, 29.88],
  [-101.4, 29.77],
  [-101.25, 29.52],
  [-100.68, 29.1],
  [-100.29, 28.28],
  [-99.93, 27.98],
  [-99.88, 27.8],
  [-99.51, 27.57],
  [-99.45, 27.02],
  [-99.09, 26.4],
  [-98.19, 26.05],
  [-97.66, 26.04],
  [-97.37, 25.84],
  [-97.29, 25.96],
  [-97.09, 25.96],
  [-97.32, 27.11],
  [-96.96, 27.88],
  [-96.24, 28.42],
  [-95.33, 28.83],
  [-94.63, 29.4],
  [-94.03, 29.63],
  [-93.81, 29.6],
  [-93.93, 29.81],
  [-93.7, 30.06],
  [-93.74, 30.54],
  [-93.51, 31.03],
  [-93.83, 31.59],
  [-93.82, 31.78],
  [-94.04, 31.99],
  [-94.07, 33.58],
  [-94.39, 33.55],
  [-95.22, 33.96],
  [-95.29, 33.87],
  [-96, 33.87],
  [-96.35, 33.69],
  [-96.66, 33.92],
  [-96.76, 33.82],
  [-96.99, 33.95],
  [-97.13, 33.72],
  [-97.21, 33.92],
  [-97.43, 33.82],
  [-97.67, 33.99],
  [-97.87, 33.85],
  [-98.11, 34.15],
  [-98.42, 34.08],
].map(([longitude, latitude]) => ({ latitude, longitude }));

function globePoint(three: ThreeModule, point: RoutePoint, radius: number) {
  const latitude = (point.latitude * Math.PI) / 180;
  const longitude = (point.longitude * Math.PI) / 180;
  return new three.Vector3(
    radius * Math.cos(latitude) * Math.cos(longitude),
    radius * Math.sin(latitude),
    // Negative Z keeps the camera-facing Texas view in the conventional
    // north-up, east-right orientation.
    -radius * Math.cos(latitude) * Math.sin(longitude),
  );
}

function countryOutlines(three: ThreeModule, radius: number) {
  const topology = worldCountries as unknown as { objects: { countries: unknown } };
  const boundaries = mesh(topology as never, topology.objects.countries as never) as unknown as {
    coordinates: [number, number][][];
  };
  const group = new three.Group();
  const material = new three.LineBasicMaterial({
    color: 0x8fa5b5,
    transparent: true,
    opacity: 0.62,
  });
  for (const coordinateLine of boundaries.coordinates) {
    const points = coordinateLine.map(([longitude, latitude]) =>
      globePoint(three, { latitude, longitude }, radius),
    );
    if (points.length > 1)
      group.add(new three.Line(new three.BufferGeometry().setFromPoints(points), material));
  }
  return { group, material };
}

function lineOnGlobe(three: ThreeModule, points: RoutePoint[], color: number, radius: number) {
  const geometry = new three.BufferGeometry().setFromPoints(
    points.map((point) => globePoint(three, point, radius)),
  );
  return new three.Line(
    geometry,
    new three.LineBasicMaterial({ color, transparent: true, opacity: 0.92 }),
  );
}

function routeOnGlobe(three: ThreeModule, points: RoutePoint[], showArrows: boolean) {
  const routePoints = points.map((point) => globePoint(three, point, 1.605));
  const path = new three.CurvePath<import("three").Vector3>();
  for (let index = 1; index < routePoints.length; index += 1)
    path.add(new three.LineCurve3(routePoints[index - 1], routePoints[index]));

  const route = new three.Group();
  route.add(
    new three.Mesh(
      new three.TubeGeometry(path, Math.max(32, points.length * 5), 0.0025, 6, false),
      new three.MeshBasicMaterial({ color: 0xf0b653, transparent: true }),
    ),
  );
  if (showArrows) {
    for (const offset of [0.33, 0.68]) {
      const position = path.getPointAt(offset);
      const tangent = path.getTangentAt(offset).normalize();
      const arrow = new three.Mesh(
        new three.ConeGeometry(0.0018, 0.006, 3),
        new three.MeshBasicMaterial({ color: 0xf5c979, transparent: true }),
      );
      arrow.position.copy(position).addScaledVector(position.clone().normalize(), 0.006);
      arrow.quaternion.setFromUnitVectors(new three.Vector3(0, 1, 0), tangent);
      route.add(arrow);
    }
  }
  return route;
}

function locationLabel(three: ThreeModule, text: string, color: string) {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.font = "500 20px ui-sans-serif, system-ui";
  const width = Math.ceil(context.measureText(text).width) + 18;
  canvas.width = width;
  canvas.height = 34;
  context.font = "500 20px ui-sans-serif, system-ui";
  context.fillStyle = color;
  context.fillText(text, 9, 24);
  const texture = new three.CanvasTexture(canvas);
  texture.colorSpace = three.SRGBColorSpace;
  const sprite = new three.Sprite(
    new three.SpriteMaterial({ map: texture, transparent: true, depthTest: true }),
  );
  sprite.scale.set((width / 34) * 0.019, 0.019, 1);
  return sprite;
}

function setGroupOpacity(group: import("three").Object3D, opacity: number) {
  group.traverse((item) => {
    const renderable = item as import("three").Mesh;
    const materials = Array.isArray(renderable.material)
      ? renderable.material
      : [renderable.material];
    for (const material of materials) {
      if (!material) continue;
      material.transparent = true;
      material.opacity = opacity;
    }
  });
}

function easeInOut(progress: number) {
  return progress < 0.5
    ? 4 * progress * progress * progress
    : 1 - Math.pow(-2 * progress + 2, 3) / 2;
}

export function GlobeToTexasScene({
  corridor,
  isRoadRoute,
  pickupLabel,
  deliveryLabel,
  reducedMotion,
  resetKey,
  autoPlay,
  immediateIntro,
}: SceneProps) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = host.current;
    if (!element || typeof WebGLRenderingContext === "undefined") return;
    let cancelled = false;
    let frame = 0;
    let cleanup = () => undefined;

    void import("three").then((three) => {
      if (cancelled) return;
      const scene = new three.Scene();
      const camera = new three.PerspectiveCamera(28, 1, 0.1, 100);
      camera.position.set(0, 0, 6.1);
      let renderer: import("three").WebGLRenderer;
      try {
        renderer = new three.WebGLRenderer({ antialias: true, alpha: true });
      } catch {
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.domElement.className = "route-geography__canvas";
      element.append(renderer.domElement);

      const globe = new three.Group();
      const globeMaterial = new three.MeshBasicMaterial({ color: 0x0d1a28 });
      globe.add(new three.Mesh(new three.SphereGeometry(1.55, 36, 24), globeMaterial));
      const wireframeMaterial = new three.LineBasicMaterial({
        color: 0x3d5a70,
        transparent: true,
        opacity: 0.08,
      });
      globe.add(
        new three.LineSegments(
          new three.WireframeGeometry(new three.SphereGeometry(1.57, 18, 12)),
          wireframeMaterial,
        ),
      );
      const countries = countryOutlines(three, 1.59);
      globe.add(countries.group);
      const texas = new three.Group();
      texas.add(lineOnGlobe(three, TEXAS_OUTLINE, 0xe4edf2, 1.595));
      texas.add(routeOnGlobe(three, corridor, isRoadRoute));
      for (const [index, point] of [corridor[0], corridor.at(-1)].entries()) {
        if (!point) continue;
        const marker = new three.Mesh(
          new three.SphereGeometry(index === 0 ? 0.003 : 0.0027, 16, 12),
          new three.MeshBasicMaterial({
            color: index === 0 ? 0xf3c46d : 0xe8e6df,
            transparent: true,
          }),
        );
        marker.position.copy(globePoint(three, point, 1.612));
        texas.add(marker);
        const label = locationLabel(
          three,
          index === 0 ? pickupLabel : deliveryLabel,
          index === 0 ? "#f3c46d" : "#e8e6df",
        );
        if (label) {
          label.position.copy(globePoint(three, point, 1.617));
          label.position.y += index === 0 ? 0.016 : -0.016;
          texas.add(label);
        }
      }
      globe.add(texas);
      const startRotation = new three.Euler(-0.32, 3.8, 0);
      const texasCenter = globePoint(three, { latitude: 31, longitude: -99 }, 1);
      const targetY = Math.atan2(-texasCenter.x, texasCenter.z);
      const targetZ = -Math.sin(targetY) * texasCenter.x + Math.cos(targetY) * texasCenter.z;
      const targetRotation = new three.Euler(Math.atan2(texasCenter.y, targetZ), targetY, 0);
      globe.rotation.copy(startRotation);
      scene.add(globe);
      setGroupOpacity(texas, reducedMotion && !immediateIntro ? 1 : 0);

      let viewProgress = 0;
      let targetViewProgress = 0;
      let introPlaying = false;
      let introHandled = false;
      let previousFrame = performance.now();
      let rotateX = 0;
      let rotateY = 0;
      let targetRotateX = 0;
      let targetRotateY = 0;
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      const resize = () => {
        const { width, height } = element.getBoundingClientRect();
        if (!width || !height) return;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      };
      const onWheel = (event: WheelEvent) => {
        event.preventDefault();
        introPlaying = false;
        introHandled = true;
        const delta =
          event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * 16 : event.deltaY;
        targetViewProgress = Math.min(1, Math.max(0, targetViewProgress - delta * 0.003));

        // Dragging is exploratory. Scrolling in is a deliberate request to inspect
        // this active load, so ease any manual rotation back to its Texas view.
        if (delta < 0) {
          targetRotateX = 0;
          targetRotateY = 0;
        }
      };
      const onDown = (event: PointerEvent) => {
        introPlaying = false;
        introHandled = true;
        targetViewProgress = viewProgress;
        dragging = true;
        lastX = event.clientX;
        lastY = event.clientY;
        renderer.domElement.setPointerCapture(event.pointerId);
      };
      const onMove = (event: PointerEvent) => {
        if (!dragging) return;
        targetRotateY += (event.clientX - lastX) * 0.006;
        targetRotateX += (event.clientY - lastY) * 0.004;
        lastX = event.clientX;
        lastY = event.clientY;
      };
      const onUp = () => {
        dragging = false;
      };
      const animate = (now: number) => {
        const elapsed = Math.min(100, now - previousFrame);
        previousFrame = now;
        if (reducedMotion && !immediateIntro) viewProgress = targetViewProgress;
        else {
          const duration = introPlaying ? 900 : 180;
          viewProgress += (targetViewProgress - viewProgress) * Math.min(1, elapsed / duration);
          if (introPlaying && Math.abs(targetViewProgress - viewProgress) < 0.005)
            introPlaying = false;
        }
        const zoomProgress = viewProgress;
        const rotationProgress = easeInOut(viewProgress);
        const mapReveal = easeInOut(Math.min(1, Math.max(0, (viewProgress - 0.38) / 0.62)));
        const rotationDamping = reducedMotion && !immediateIntro ? 1 : Math.min(1, elapsed / 140);
        rotateX += (targetRotateX - rotateX) * rotationDamping;
        rotateY += (targetRotateY - rotateY) * rotationDamping;
        globe.rotation.set(
          startRotation.x + (targetRotation.x - startRotation.x) * rotationProgress + rotateX,
          startRotation.y + (targetRotation.y - startRotation.y) * rotationProgress + rotateY,
          startRotation.z + (targetRotation.z - startRotation.z) * rotationProgress,
        );
        const globeScale = 0.54 + zoomProgress * 1.96;
        const detailZoom = Math.min(1, Math.max(0, (globeScale - 1.9) / 0.26));
        globe.scale.setScalar(globeScale);
        wireframeMaterial.opacity = 0.04 - detailZoom * 0.02;
        countries.material.opacity = 0.62 - detailZoom * 0.25;
        setGroupOpacity(texas, mapReveal * detailZoom);
        renderer.render(scene, camera);
        frame = requestAnimationFrame(animate);
      };

      const observer = new ResizeObserver(resize);
      let visibilityObserver: IntersectionObserver | undefined;
      const beginIntro = () => {
        if (introHandled || !autoPlay || (reducedMotion && !immediateIntro)) return;
        introHandled = true;
        introPlaying = true;
        targetViewProgress = 1;
      };
      observer.observe(element);
      renderer.domElement.addEventListener("wheel", onWheel, { passive: false });
      renderer.domElement.addEventListener("pointerdown", onDown);
      renderer.domElement.addEventListener("pointermove", onMove);
      renderer.domElement.addEventListener("pointerup", onUp);
      resize();
      if (autoPlay && (!reducedMotion || immediateIntro)) {
        if (immediateIntro) beginIntro();
        else if ("IntersectionObserver" in window) {
          visibilityObserver = new IntersectionObserver(
            ([entry]) => {
              if (entry?.isIntersecting && entry.intersectionRatio >= 0.5) beginIntro();
            },
            { threshold: 0.5 },
          );
          visibilityObserver.observe(element);
        } else beginIntro();
      }
      frame = requestAnimationFrame(animate);
      cleanup = () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
        visibilityObserver?.disconnect();
        renderer.domElement.removeEventListener("wheel", onWheel);
        renderer.domElement.removeEventListener("pointerdown", onDown);
        renderer.domElement.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("pointerup", onUp);
        scene.traverse((item) => {
          const mesh = item as import("three").Mesh;
          mesh.geometry?.dispose();
          const material = mesh.material;
          if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
          else material?.dispose();
        });
        renderer.dispose();
        renderer.domElement.remove();
      };
    });
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [
    autoPlay,
    corridor,
    deliveryLabel,
    immediateIntro,
    isRoadRoute,
    pickupLabel,
    reducedMotion,
    resetKey,
  ]);

  return <div ref={host} className="route-geography__scene" aria-hidden="true" />;
}
