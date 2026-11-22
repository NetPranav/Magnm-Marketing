export const SOCIAL_VIEWPORT_WIDTH = 1280;
export const SOCIAL_VIEWPORT_HEIGHT = 800;

export interface ViewportPoint {
  x: number;
  y: number;
}

export interface ElementRectLike {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Map a pointer tap inside an object-fit:contain container to 1280x800 browser coordinates,
 * accounting for letterbox or pillarbox padding. Returns null if outside the rendered frame.
 */
export function mapClientPointToViewport(
  clientX: number,
  clientY: number,
  rect: ElementRectLike,
  viewportWidth = SOCIAL_VIEWPORT_WIDTH,
  viewportHeight = SOCIAL_VIEWPORT_HEIGHT,
): ViewportPoint | null {
  if (
    !Number.isFinite(clientX) ||
    !Number.isFinite(clientY) ||
    !rect ||
    rect.width <= 0 ||
    rect.height <= 0 ||
    viewportWidth <= 0 ||
    viewportHeight <= 0
  ) {
    return null;
  }

  const scale = Math.min(rect.width / viewportWidth, rect.height / viewportHeight);
  const renderedWidth = viewportWidth * scale;
  const renderedHeight = viewportHeight * scale;
  if (renderedWidth <= 0 || renderedHeight <= 0) {
    return null;
  }

  const offsetX = (rect.width - renderedWidth) / 2;
  const offsetY = (rect.height - renderedHeight) / 2;
  const relX = clientX - rect.left - offsetX;
  const relY = clientY - rect.top - offsetY;

  if (relX < 0 || relY < 0 || relX > renderedWidth || relY > renderedHeight) {
    return null;
  }

  const x = Math.min(
    viewportWidth,
    Math.max(0, Math.round((relX / renderedWidth) * viewportWidth * 100) / 100),
  );
  const y = Math.min(
    viewportHeight,
    Math.max(0, Math.round((relY / renderedHeight) * viewportHeight * 100) / 100),
  );
  return { x, y };
}
