App({
    options: {
        debug: false
    },
    /**
     * 当wap2app初始化完成时，会触发 onLaunch
     */
    onLaunch: function() {
        console.log('launch');
        // ★ 状态栏避让由【网页层】负责，本壳不要重复让位。
        //   manifest.json 里 plus.statusbar.immersed = "supportedDevice"
        //   （沉浸式）+ launchwebview.contentAdjust = false，
        //   窗体铺到屏幕顶部，网页顶栏会压在手机系统状态栏下方。
        //   网页（server/static/index.html）在 HTML5+ 运行时里读
        //   plus.navigator.getSafeAreaInsets().deviceTop（回退
        //   getStatusbarHeight()），注入 --app-status-h 让顶栏内容下移。
        //   ★ 若以后改成壳层让位（launchwebview 的 top / offset），
        //     必须先关掉网页层的避让，否则会出现「双重偏移」。
        try {
            var _sb = plus.navigator.getStatusbarHeight();
            console.log('Statusbar Height: ' + _sb);
        } catch (e) { }
    },
    /**
     * 当wap2app启动，或从后台进入前台显示，会触发 onShow
     */
    onShow: function() {
        console.log('show');
    },
    /**
     * 当wap2app从前台进入后台，会触发 onHide
     */
    onHide: function() {
        console.log('hide');
    }
});
Page('__W2A__bapimine.click', { //首页扩展配置
    onShow: function() {

    },
    onClose: function() {

    }
});