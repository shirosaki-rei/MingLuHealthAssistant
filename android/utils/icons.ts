// 图标。字形码位来自 Material Symbols Rounded 的子集，字体文件在 static/font/minglu-icons.ttf。
//
// 为什么用字体而不是 SVG：App 平台不支持内联 svg 标签，`<image src="*.svg">` 也没法用 css
// 换色——选中/未选中就得备两份文件。字体可以用 color 控制，一个文件全搞定，也是官方文档
// 给的跨端写法（unicode 直显，App 不支持伪元素所以不能走 content）。
//
// 加新图标：改 scripts/build-iconfont.py 里的 ICONS，跑 build，把下面的码位补上。

const ICONS: Record<string, string> = {
	add: '\ue145',
	arrow_back: '\ue5c4',
	assignment: '\ue85d',
	auto_awesome: '\ue65f',
	check: '\ue668',
	chevron_right: '\ue5cc',
	close: '\ue5cd',
	cloud_upload: '\ue2c3',
	delete: '\ue92e',
	description: '\ue873',
	edit: '\uf097',
	emergency: '\ue1eb',
	event_available: '\ue614',
	favorite: '\ue87e',
	forum: '\ue8af',
	group: '\uea21',
	help: '\ue8fd',
	home: '\ue9b2',
	info: '\ue88e',
	local_hospital: '\ue548',
	lock: '\ue899',
	logout: '\ue9ba',
	medication: '\uf033',
	monitor_heart: '\ueaa2',
	notifications: '\ue7f5',
	payments: '\uef63',
	pending: '\uef64',
	person: '\uf0d3',
	photo_camera: '\ue412',
	psychology: '\uea4a',
	repeat: '\ue040',
	schedule: '\uefd6',
	search: '\uef7a',
	send: '\ue163',
	settings: '\ue8b8',
	stethoscope: '\uf805',
	timeline: '\ue922',
	travel_explore: '\ue2db',
	warning: '\uf083'
}

/** 取图标的 unicode 字面量，直接在模板里 {{ ic('home') }}。
 *  名字写错返回空字符串——比渲染成一个方框好看，也更容易在自查时发现。 */
export function ic (name: string): string {
	return ICONS[name] ?? ''
}
