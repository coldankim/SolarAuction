<?php
// 호스팅 기본 index.php가 index.html보다 우선이라, 화면(index.html)을 그대로 보여준다
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-cache');
readfile(__DIR__ . '/index.html');
