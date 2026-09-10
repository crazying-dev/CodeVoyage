<script setup lang="ts">
import LogoImage from '../assets/logo.png'

const MIT_license = "MIT License\n" +
    "\n" +
    "Copyright (c) 2026 crazying-dev\n" +
    "\n" +
    "Permission is hereby granted, free of charge, to any person obtaining a copy\n" +
    "of this software and associated documentation files (the \"Software\"), to deal\n" +
    "in the Software without restriction, including without limitation the rights\n" +
    "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n" +
    "copies of the Software, and to permit persons to whom the Software is\n" +
    "furnished to do so, subject to the following conditions:\n" +
    "\n" +
    "The above copyright notice and this permission notice shall be included in all\n" +
    "copies or substantial portions of the Software.\n" +
    "\n" +
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n" +
    "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n" +
    "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n" +
    "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n" +
    "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n" +
    "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n" +
    "SOFTWARE."

</script>

<template>
  <footer>
    <div class="footer-menu">
      <div class="footer-ol">
        <img :src="LogoImage" alt="" id="footer-logo">
        <h2  style="color: black">CodeVoyage</h2>
      </div>

      <div class="footer-ol">
        <h2  style="color: black">people</h2>
        <a href="/about#collaborator">collaborator</a>
        <br>
        <a href="/about#Contributors">Contributors</a>
      </div>

      <div class="footer-ol">
        <h2  style="color: black">about repository</h2>
        <a href="//github.com/crazying-dev/CodeVoyage" style="display: flex;">
          <svg width="24" height="24"><use href="/icons.svg#github-icon"/></svg>
          Github
        </a>
        <a href="//github.com/crazying-dev/CodeVoyage"><img src="https://img.shields.io/github/stars/crazying-dev/CodeVoyage" alt=""></a>
        <br>
        <a href="//github.com/crazying-dev/CodeVoyage?tab=MIT-1-ov-file#" :title="MIT_license">license</a>
        <br>
      </div>
    </div>
    <div id="C-logo">
      © 2026 crazying‑dev · Released under the MIT License
    </div>
  </footer>
</template>