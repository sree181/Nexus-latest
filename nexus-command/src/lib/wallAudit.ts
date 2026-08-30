export function underSizeText(root: ParentNode = document, floor = 28): Element[] {
  return [...root.querySelectorAll('*')].filter(element => {
    const text = (element as HTMLElement).innerText?.trim();
    if (!text) return false;
    const size = parseFloat(getComputedStyle(element).fontSize);
    return Number.isFinite(size) && size < floor;
  });
}

export function undersizedTouch(root: ParentNode = document, min = 128): Element[] {
  return [...root.querySelectorAll('button, [role="button"]')].filter(element => {
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return false;
    return Math.min(box.width, box.height) < min;
  });
}

export function frameScrollHeight(): number {
  return document.scrollingElement?.scrollHeight ?? 0;
}
