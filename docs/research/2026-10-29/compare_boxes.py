import re, json, sys, statistics as st
import pdfplumber
dom_path, pdf_path = sys.argv[1], sys.argv[2]
d = open(dom_path).read()
boxes = json.loads(re.search(r'<script type="application/json" id="boxes">(.*?)</script>', d, re.S).group(1))
pdf = pdfplumber.open(pdf_path); page = pdf.pages[0]
chars = page.chars
print('pages', len(pdf.pages), 'pdf chars', len(chars), 'dom chars', len(boxes))
print('multi-char / ligature glyphs in pdf:', [c['text'] for c in chars if len(c['text']) > 1 or 0xFB00 <= ord(c['text'][0]) <= 0xFB06])
dom_ns = [b for b in boxes if not b[2].isspace()]
pdf_ns = [c for c in chars if not c['text'].isspace()]
print('non-space dom', len(dom_ns), 'non-space pdf', len(pdf_ns), 'pdf space chars', len(chars) - len(pdf_ns))
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua else 0
ious, dx, dy, mism = [], [], [], 0
for a, c in zip(dom_ns, pdf_ns):
    if a[2] != c['text']:
        mism += 1
        if mism < 6: print('mismatch', repr(a[2]), repr(c['text']))
        continue
    db = [v * 0.75 for v in a[3:7]]; pb = [c['x0'], c['top'], c['x1'], c['bottom']]
    ious.append(iou(db, pb)); dx.append(abs(db[0] - pb[0])); dy.append(abs(db[1] - pb[1]))
print('mismatches', mism, 'matched', len(ious))
print('char IoU median %.3f p5 %.3f min %.3f' % (st.median(ious), sorted(ious)[len(ious)//20], min(ious)))
print('x0 err pt median %.3f max %.3f | top err pt median %.3f max %.3f' % (st.median(dx), max(dx), st.median(dy), max(dy)))
print('pdf words tail:', [w['text'] for w in page.extract_words()][-14:])
