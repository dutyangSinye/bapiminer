<template>
	<view class="wrap">
		<web-view :src="url"></web-view>
	</view>
</template>

<script>
	export default {
		data() {
			return {
				// 扒皮矿工挖矿群控中控地址（新服务器 Docker）
				// 看板首次打开会引导输入口令，保存在本机 WebView 存储，之后免输
				url: 'http://mine.sinyerobot.com:8099/'
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
