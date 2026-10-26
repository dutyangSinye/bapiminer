<template>
	<view class="wrap">
		<!--
			★ 状态栏避让由【网页层】负责，本壳不要重复让位。
			  原因：本壳是 navigationStyle:custom 的沉浸式窗体，<web-view>
			  默认铺满整页（顶栏会压在手机系统状态栏下方）。
			  网页（server/static/index.html）在 HTML5+ 运行时里读
			  plus.navigator.getSafeAreaInsets().deviceTop（回退
			  getStatusbarHeight()），注入 --app-status-h 让顶栏内容下移。
			  ★ 若以后想改成壳层让位（setStyle top / 占位 view），
			    必须先关掉网页层的避让，否则会出现「双重偏移」，
			    顶栏下方多出一段空白。
		-->
		<web-view :src="url"></web-view>
	</view>
</template>

<script>
	export default {
		data() {
			return {
				// 扒皮矿工挖矿群控中控地址（新服务器 Docker）
				// 看板首次打开会引导输入口令，保存在本机 WebView 存储，之后免输
				url: 'http://bapimine.click:8099/'
			}
		}
	}
</script>

<style>
	.wrap {
		width: 100%;
		height: 100%;
	}
</style>
