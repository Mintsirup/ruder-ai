const button = document.getElementById("count");
let count = 0;
button.addEventListener("click", () => { count++; button.textContent = count; });
