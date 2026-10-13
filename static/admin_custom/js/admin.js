document.addEventListener('DOMContentLoaded', function(){
  var btn = document.getElementById('toggleSidebar');
  var sidebar = document.getElementById('nzSidebar');
  if (btn && sidebar){
    btn.addEventListener('click', function(){
      if (sidebar.style.display === 'none'){
        sidebar.style.display = 'block';
      } else {
        sidebar.style.display = 'none';
      }
    });
  }
});