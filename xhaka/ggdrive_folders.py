def folder_list_filted(data):
    items = data['items']
    own_folders = [
        {
            'id': item['id'],
            'name': item['title'],
            'parent_ids': [
                pitems['id']
                for pitems in item['parents']
                if not pitems['isRoot']
            ],
        }
        for item in items
        if item['userPermission']['role'] == 'owner'
        and not item['labels']['trashed']
    ]

    def find_more(fid):
        for item in items:
            if fid in [
                pitems['id']
                for pitems in item['parents']
                if not pitems['isRoot']
            ]:
                fdata = {
                    'id': item['id'],
                    'name': item['title'],
                    'parent_ids': [
                        pitems['id']
                        for pitems in item['parents']
                        if not pitems['isRoot']
                    ],
                }
                if fdata not in own_folders:
                    own_folders.append(fdata)
                    find_more(item['id'])

    for f in own_folders:
        find_more(f['id'])
    return own_folders


def get_folder_hierarchy(folders):
    def findx(f, pid, folders, result):
        for f_ in folders:
            if f_['id'] == pid:
                result.insert(0, f_['id'])
                for pid_ in f_['parent_ids']:
                    findx(folder, pid_, folders, result)

    folder_paths = []
    folder_id_name_pairs = {}
    for folder in folders:
        folder_id_name_pairs[folder['id']] = folder['name']
        result = []
        for pid in folder['parent_ids']:
            findx(folder, pid, folders, result)
            result.append(folder['id'])
            folder_paths.append(result)
        if not result:
            result.append(folder['id'])
            folder_paths.append(result)

    return sorted(folder_paths), folder_id_name_pairs
