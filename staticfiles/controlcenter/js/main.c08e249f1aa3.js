document.addEventListener('DOMContentLoaded', function () {
  // Mobile sidebar menu toggle
  var menuToggle = document.getElementById('wp-menu-toggle');
  var adminMenu = document.getElementById('adminmenu');
  if (menuToggle && adminMenu) {
    menuToggle.addEventListener('click', function () {
      adminMenu.classList.toggle('open');
    });
  }

  // Select all checkboxes for bulk actions in WP List Tables
  var selectAllTop = document.getElementById('cb-select-all-1');
  var selectAllBottom = document.getElementById('cb-select-all-2');
  var rowCheckboxes = document.querySelectorAll('.wp-list-table input[name="selected"]');

  function syncCheckboxes(checked) {
    rowCheckboxes.forEach(function (cb) {
      cb.checked = checked;
    });
    if (selectAllTop) selectAllTop.checked = checked;
    if (selectAllBottom) selectAllBottom.checked = checked;
  }

  if (selectAllTop) {
    selectAllTop.addEventListener('change', function () {
      syncCheckboxes(this.checked);
    });
  }
  if (selectAllBottom) {
    selectAllBottom.addEventListener('change', function () {
      syncCheckboxes(this.checked);
    });
  }

  // Dismissible notices
  var notices = document.querySelectorAll('.notice.is-dismissible');
  notices.forEach(function (notice) {
    var dismissBtn = document.createElement('button');
    dismissBtn.type = 'button';
    dismissBtn.className = 'notice-dismiss';
    dismissBtn.innerHTML = '<span class="screen-reader-text">Dismiss this notice.</span>';
    dismissBtn.addEventListener('click', function () {
      notice.style.display = 'none';
    });
    notice.appendChild(dismissBtn);
  });

  // Dynamic Inlines ("Add another [Download Quality/Subtitle]")
  var addInlineButtons = document.querySelectorAll('.add-inline-row-btn');
  addInlineButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var prefix = this.getAttribute('data-prefix');
      var group = document.getElementById(prefix + '-group');
      if (!group) return;

      var totalFormsInput = group.querySelector('input[name="' + prefix + '-TOTAL_FORMS"]');
      if (!totalFormsInput) return;

      var currentCount = parseInt(totalFormsInput.value, 10);
      var tbody = group.querySelector('.inline-tbody');
      var templateContainer = group.querySelector('.empty-form-template');

      if (tbody && templateContainer) {
        var templateHtml = templateContainer.querySelector('tr').outerHTML;
        // Replace __prefix__ with currentCount index
        var newRowHtml = templateHtml.replace(/__prefix__/g, currentCount);
        tbody.insertAdjacentHTML('beforeend', newRowHtml);
        totalFormsInput.value = currentCount + 1;
      }
    });
  });
});
