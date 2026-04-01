/**
 * BillMap PDF Viewer — renders PDFs with region highlighting and drawing overlays.
 *
 * Features:
 * - Renders PDF pages using PDF.js
 * - Draws colored rectangles for extraction regions (green = mapped, blue = suggested)
 * - Drawing mode: user draws rectangles to define new extraction regions
 * - Page navigation
 */

const pdfjsLib = window['pdfjs-dist/build/pdf'] || null;

// PDF.js worker — use CDN
if (typeof pdfjsLib !== 'undefined' && pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs';
}

class PDFViewer {
    constructor(container) {
        this.container = container;
        this.pdfCanvas = container.querySelector('#pdf-canvas');
        this.overlayCanvas = container.querySelector('#overlay-canvas');
        this.drawingCanvas = container.querySelector('#drawing-canvas');
        this.pdfCtx = this.pdfCanvas.getContext('2d');
        this.overlayCtx = this.overlayCanvas.getContext('2d');

        this.pdfDoc = null;
        this.currentPage = 1;
        this.totalPages = 0;
        this.scale = 1.5;
        this.regions = [];
        this.highlightedField = null;

        // Drawing state
        this.drawing = false;
        this.drawStart = null;
        this.drawEnd = null;
        this.onRegionDrawn = null; // callback(region)

        this.init();
    }

    async init() {
        const pdfUrl = this.container.dataset.pdfUrl;
        const regionsJson = this.container.dataset.regions;

        if (regionsJson) {
            try {
                this.regions = JSON.parse(regionsJson);
            } catch (e) {
                console.warn('Failed to parse regions:', e);
            }
        }

        if (pdfUrl) {
            await this.loadPDF(pdfUrl);
        }

        this.setupNavigation();
        this.setupDrawing();
    }

    async loadPDF(url) {
        try {
            // Use dynamic import for PDF.js if not loaded globally
            let lib = pdfjsLib;
            if (!lib) {
                lib = await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs');
                lib.GlobalWorkerOptions.workerSrc =
                    'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs';
            }

            this.pdfDoc = await lib.getDocument(url).promise;
            this.totalPages = this.pdfDoc.numPages;
            this.updatePageInfo();
            await this.renderPage(this.currentPage);
        } catch (e) {
            console.error('Failed to load PDF:', e);
        }
    }

    async renderPage(pageNum) {
        if (!this.pdfDoc) return;

        const page = await this.pdfDoc.getPage(pageNum);
        const viewport = page.getViewport({ scale: this.scale });

        // Size all canvases to match
        [this.pdfCanvas, this.overlayCanvas].forEach(c => {
            c.width = viewport.width;
            c.height = viewport.height;
        });

        if (this.drawingCanvas) {
            this.drawingCanvas.width = viewport.width;
            this.drawingCanvas.height = viewport.height;
        }

        // Render PDF page
        await page.render({
            canvasContext: this.pdfCtx,
            viewport: viewport,
        }).promise;

        // Draw region overlays
        this.drawRegions();
    }

    drawRegions() {
        const ctx = this.overlayCtx;
        const w = this.overlayCanvas.width;
        const h = this.overlayCanvas.height;

        ctx.clearRect(0, 0, w, h);

        for (const region of this.regions) {
            if (region.page !== undefined && region.page !== this.currentPage - 1) continue;

            const x = region.x * w;
            const y = region.y * h;
            const rw = region.w * w;
            const rh = region.h * h;

            // Color based on type
            let color = 'rgba(13, 110, 253, 0.2)';  // blue = suggested
            let borderColor = 'rgba(13, 110, 253, 0.8)';

            if (region.type === 'mapped') {
                color = 'rgba(25, 135, 84, 0.2)';     // green = mapped
                borderColor = 'rgba(25, 135, 84, 0.8)';
            }

            if (region.field === this.highlightedField) {
                color = 'rgba(255, 193, 7, 0.3)';      // yellow = highlighted
                borderColor = 'rgba(255, 193, 7, 1.0)';
            }

            ctx.fillStyle = color;
            ctx.fillRect(x, y, rw, rh);

            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 2;
            ctx.strokeRect(x, y, rw, rh);

            // Label
            if (region.field) {
                ctx.fillStyle = borderColor;
                ctx.font = '11px -apple-system, sans-serif';
                ctx.fillText(region.field, x + 2, y - 3);
            }
        }
    }

    highlightRegion(fieldName) {
        this.highlightedField = fieldName;
        this.drawRegions();
    }

    clearHighlight() {
        this.highlightedField = null;
        this.drawRegions();
    }

    setupNavigation() {
        const prevBtn = document.getElementById('prev-page');
        const nextBtn = document.getElementById('next-page');

        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (this.currentPage > 1) {
                    this.currentPage--;
                    this.renderPage(this.currentPage);
                    this.updatePageInfo();
                }
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (this.currentPage < this.totalPages) {
                    this.currentPage++;
                    this.renderPage(this.currentPage);
                    this.updatePageInfo();
                }
            });
        }
    }

    updatePageInfo() {
        const info = document.getElementById('page-info');
        if (info) {
            info.textContent = `Page ${this.currentPage} / ${this.totalPages}`;
        }
    }

    setupDrawing() {
        if (!this.drawingCanvas) return;

        const drawCtx = this.drawingCanvas.getContext('2d');

        this.drawingCanvas.addEventListener('mousedown', (e) => {
            const rect = this.drawingCanvas.getBoundingClientRect();
            this.drawing = true;
            this.drawStart = {
                x: e.clientX - rect.left,
                y: e.clientY - rect.top,
            };
        });

        this.drawingCanvas.addEventListener('mousemove', (e) => {
            if (!this.drawing) return;
            const rect = this.drawingCanvas.getBoundingClientRect();
            this.drawEnd = {
                x: e.clientX - rect.left,
                y: e.clientY - rect.top,
            };

            // Draw preview rectangle
            drawCtx.clearRect(0, 0, this.drawingCanvas.width, this.drawingCanvas.height);
            const dx = this.drawStart.x;
            const dy = this.drawStart.y;
            const dw = this.drawEnd.x - dx;
            const dh = this.drawEnd.y - dy;

            drawCtx.fillStyle = 'rgba(13, 110, 253, 0.15)';
            drawCtx.fillRect(dx, dy, dw, dh);
            drawCtx.strokeStyle = 'rgba(13, 110, 253, 0.9)';
            drawCtx.lineWidth = 2;
            drawCtx.setLineDash([5, 3]);
            drawCtx.strokeRect(dx, dy, dw, dh);
            drawCtx.setLineDash([]);
        });

        this.drawingCanvas.addEventListener('mouseup', (e) => {
            if (!this.drawing || !this.drawStart || !this.drawEnd) return;
            this.drawing = false;

            const w = this.drawingCanvas.width;
            const h = this.drawingCanvas.height;

            // Normalize to 0-1 range
            const region = {
                page: this.currentPage - 1,
                x: Math.min(this.drawStart.x, this.drawEnd.x) / w,
                y: Math.min(this.drawStart.y, this.drawEnd.y) / h,
                w: Math.abs(this.drawEnd.x - this.drawStart.x) / w,
                h: Math.abs(this.drawEnd.y - this.drawStart.y) / h,
            };

            // Clear drawing canvas
            const drawCtx = this.drawingCanvas.getContext('2d');
            drawCtx.clearRect(0, 0, w, h);

            // Fire callback
            if (this.onRegionDrawn) {
                this.onRegionDrawn(region);
            }

            // Add to regions for display
            this.drawStart = null;
            this.drawEnd = null;
        });
    }

    addRegion(region) {
        this.regions.push(region);
        this.drawRegions();
    }

    setRegions(regions) {
        this.regions = regions;
        this.drawRegions();
    }
}

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('pdf-container');
    if (container) {
        window.pdfViewer = new PDFViewer(container);
    }
});

// Expose to Datastar via window functions
window.$$highlightRegion = function(fieldName) {
    if (window.pdfViewer) window.pdfViewer.highlightRegion(fieldName);
};

window.$$clearHighlight = function() {
    if (window.pdfViewer) window.pdfViewer.clearHighlight();
};

window.$$startDraw = function(evt) {
    // Handled internally by PDFViewer
};

window.$$drawing = function(evt) {
    // Handled internally by PDFViewer
};

window.$$endDraw = function(evt) {
    // Handled internally by PDFViewer
};

export { PDFViewer };
