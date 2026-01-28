/**
 * Table Sorting Module
 * Enables client-side sorting of HTML tables via table header clicks
 * Supports numeric, date, and text sorting
 * Includes row click navigation to task detail pages
 */

class TableSorter {
  constructor(tableSelector, options = {}) {
    this.table = document.querySelector(tableSelector);
    if (!this.table) return;
    
    this.tbody = this.table.querySelector('tbody');
    this.headers = this.table.querySelectorAll('thead th');
    this.defaultSortColumn = options.defaultSortColumn || 1; // Priority column
    this.defaultSortOrder = options.defaultSortOrder || 'asc';
    this.rowClickCallback = options.rowClickCallback || null;
    
    this.currentSortColumn = null;
    this.currentSortOrder = 'asc';
    
    this.init();
  }
  
  init() {
    // Attach click handlers to headers
    this.headers.forEach((header, index) => {
      header.style.cursor = 'pointer';
      header.addEventListener('click', () => this.sortByColumn(index, header));
    });
    
    // Apply default sort
    this.applyDefaultSort();
    
    // Attach row click handlers
    this.attachRowClickHandlers();
  }
  
  applyDefaultSort() {
    if (this.defaultSortColumn !== null) {
      const header = this.headers[this.defaultSortColumn];
      if (header) {
        this.sortByColumn(this.defaultSortColumn, header);
      }
    }
  }
  
  sortByColumn(columnIndex, header) {
    // Determine sort order
    if (this.currentSortColumn === columnIndex) {
      // Toggle sort order if same column clicked again
      this.currentSortOrder = this.currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
      // Default to ascending for new column
      this.currentSortOrder = 'asc';
    }
    
    this.currentSortColumn = columnIndex;
    
    // Get all rows
    const rows = Array.from(this.tbody.querySelectorAll('tr'));
    
    // Sort rows
    rows.sort((rowA, rowB) => {
      const cellA = rowA.children[columnIndex]?.textContent.trim() || '';
      const cellB = rowB.children[columnIndex]?.textContent.trim() || '';
      
      return this.compareValues(cellA, cellB, this.currentSortOrder);
    });
    
    // Re-append sorted rows
    rows.forEach(row => this.tbody.appendChild(row));
    
    // Update header indicators
    this.updateHeaderIndicators(header);
  }
  
  compareValues(valueA, valueB, order) {
    // Try to parse as numbers
    const numA = parseFloat(valueA);
    const numB = parseFloat(valueB);
    
    if (!isNaN(numA) && !isNaN(numB)) {
      return order === 'asc' ? numA - numB : numB - numA;
    }
    
    // Try to parse as dates (YYYY-MM-DD format)
    const dateA = this.parseDate(valueA);
    const dateB = this.parseDate(valueB);
    
    if (dateA && dateB) {
      const comparison = dateA.getTime() - dateB.getTime();
      return order === 'asc' ? comparison : -comparison;
    }
    
    // String comparison
    const comparison = valueA.localeCompare(valueB);
    return order === 'asc' ? comparison : -comparison;
  }
  
  parseDate(dateStr) {
    // Try to parse YYYY-MM-DD format
    const match = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) {
      return new Date(match[1], parseInt(match[2]) - 1, match[3]);
    }
    return null;
  }
  
  updateHeaderIndicators(header) {
    // Remove all indicators
    this.headers.forEach(h => {
      h.dataset.sortIndicator = '';
      h.textContent = h.textContent.replace(/\s*[↑↓]$/, '');
    });
    
    // Add indicator to current header
    const indicator = this.currentSortOrder === 'asc' ? '↑' : '↓';
    header.textContent += ` ${indicator}`;
  }
  
  attachRowClickHandlers() {
    const rows = this.tbody.querySelectorAll('tr');
    rows.forEach(row => {
      row.style.cursor = 'pointer';
      row.addEventListener('click', (e) => {
        // Don't navigate if clicking on interactive elements
        if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON') {
          return;
        }
        this.handleRowClick(row);
      });
    });
  }
  
  handleRowClick(row) {
    // Get task URL from data attribute
    const taskUrl = row.dataset.taskUrl;
    if (taskUrl) {
      window.location.href = taskUrl;
      return;
    }
    
    // Fallback: try to find a link in the row
    const link = row.querySelector('a[href]');
    if (link) {
      window.location.href = link.href;
      return;
    }
    
    // Call custom callback if provided
    if (this.rowClickCallback) {
      this.rowClickCallback(row);
    }
  }
  
  destroy() {
    if (!this.table) return;
    this.headers.forEach(header => {
      header.style.cursor = 'auto';
      header.replaceWith(header.cloneNode(true));
    });
  }
}

// Initialize tables on DOM ready
document.addEventListener('DOMContentLoaded', function() {
  // Initialize My Work tables
  new TableSorter('#work-table-open', { defaultSortColumn: 2 }); // Priority column
  new TableSorter('#work-table-in-progress', { defaultSortColumn: 2 });
  new TableSorter('#work-table-due-soon', { defaultSortColumn: 2 });
  new TableSorter('#work-table-overdue', { defaultSortColumn: 2 });
  
  // Initialize Today/Backlog tables
  new TableSorter('#today-table-items', { defaultSortColumn: 2 });
  new TableSorter('#today-table-backlog', { defaultSortColumn: 2 });
});
