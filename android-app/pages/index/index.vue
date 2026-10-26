<template>
  <view class="wrap">
    <web-view :src="url"></web-view>
  </view>
</template>

<script>
export default {
  data() {
    return {
      // 中控地址；看板首次打开会引导输入口令，保存在本机 WebView
      url: 'http://bapimine.click:8099/'
    }
  }
}
</script>
